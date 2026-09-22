"""Local HTTP app for browsing an EML archive. Binds to localhost only."""

from __future__ import annotations

import json
import mimetypes
import os
import posixpath
import re
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from . import __version__
from .indexer import Indexer
from .paths import static_dir
from .parser import ParseError, decode_part_bytes, find_part_by_cid, find_part_by_index, get_html_body, get_text_body, load_message
from .sanitize import build_view_document, text_as_html
from .search import search
from .store import Store, copy_index_file, remember_db_path, resolve_db_path

STATIC_DIR = static_dir()


def reveal_command(path: Path) -> list[str]:
    """Command that opens the file's folder and selects it."""
    path = Path(path)
    if os.name == "nt":
        return ["explorer", f"/select,{path}"]
    if sys.platform == "darwin":
        return ["open", "-R", str(path)]
    return ["xdg-open", str(path.parent)]


class ExistingInstance(Exception):
    def __init__(self, port: int) -> None:
        super().__init__(f"already running on port {port}")
        self.port = port


def _json_bytes(obj: Any, status: int = 200) -> tuple[int, dict[str, str], bytes]:
    try:
        payload = json.dumps(obj, ensure_ascii=False, default=str)
    except UnicodeEncodeError:
        payload = json.dumps(obj, ensure_ascii=True, default=str)
    data = payload.encode("utf-8", errors="replace")
    return status, {"Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store"}, data


def _query_flag(qs: dict[str, list[str]], name: str) -> bool | None:
    if name not in qs:
        return None
    val = (qs[name][0] or "").strip().lower()
    if val in {"1", "true", "yes"}:
        return True
    if val in {"0", "false", "no"}:
        return False
    return None


class App:
    def __init__(self, store: Store) -> None:
        self.store = store
        self.indexer = Indexer(store)
        self.lock = threading.Lock()

    def handle(self, method: str, path: str, qs: dict[str, list[str]], body: bytes) -> tuple[int, dict[str, str], bytes]:
        parsed = urlparse(path)
        route = parsed.path
        try:
            if method == "POST" and route == "/api/settings":
                return self._save_settings(body)
            with self.store.lock:
                return self._dispatch(method, route, qs, body)
        except FileNotFoundError:
            return _json_bytes({"error": "Not found"}, 404)
        except ValueError as exc:
            return _json_bytes({"error": str(exc)}, 400)
        except OSError as exc:
            return _json_bytes({"error": str(exc)}, 500)
        except Exception as exc:  # noqa: BLE001 — never fail the process on one request
            return _json_bytes({"error": str(exc) or type(exc).__name__}, 500)

    def _dispatch(
        self, method: str, route: str, qs: dict[str, list[str]], body: bytes
    ) -> tuple[int, dict[str, str], bytes]:
        if method == "GET" and route in {"/", "/index.html"}:
            return self._static("index.html")
        if method == "GET" and route.startswith("/static/"):
            return self._static(route[len("/static/") :])
        if method == "GET" and route == "/api/health":
            return _json_bytes({"ok": True, "version": __version__})
        if method == "GET" and route == "/api/stats":
            stats = self.store.stats()
            stats["index"] = self.indexer.snapshot()
            return _json_bytes(stats)
        if method == "GET" and route == "/api/folders":
            return _json_bytes({"folders": self.store.folders(), "years": self.store.years()})
        if method == "GET" and route == "/api/tags":
            return _json_bytes({"tags": self.store.list_tags()})
        if method == "GET" and route == "/api/emails":
            return self._list_emails(qs)
        if method == "GET" and route == "/api/fs":
            return self._list_fs(qs)
        if method == "GET" and route == "/api/index":
            return _json_bytes(self.indexer.snapshot())
        if method == "POST" and route == "/api/index":
            return self._start_index(body)
        if method == "POST" and route == "/api/index/cancel":
            self.indexer.cancel()
            return _json_bytes({"ok": True})

        m = re.fullmatch(r"/api/emails/(\d+)", route)
        if m and method == "GET":
            return self._get_email(int(m.group(1)))
        if m and method == "POST":
            return self._patch_email(int(m.group(1)), body)

        m = re.fullmatch(r"/api/emails/(\d+)/html", route)
        if m and method == "GET":
            return self._email_html(int(m.group(1)), qs)

        m = re.fullmatch(r"/api/emails/(\d+)/reveal", route)
        if m and method == "POST":
            return self._reveal_email(int(m.group(1)))

        m = re.fullmatch(r"/api/emails/(\d+)/attachments/(\d+)", route)
        if m and method == "GET":
            return self._attachment(int(m.group(1)), int(m.group(2)))

        m = re.fullmatch(r"/api/emails/(\d+)/cid/(.+)", route)
        if m and method == "GET":
            return self._cid(int(m.group(1)), unquote(m.group(2)))

        if method == "POST" and route == "/api/tags":
            payload = self._body_json(body)
            tag_id = self.store.ensure_tag(str(payload.get("name") or ""), payload.get("color"))
            return _json_bytes({"id": tag_id, "tags": self.store.list_tags()})

        m = re.fullmatch(r"/api/tags/(\d+)", route)
        if m and method == "DELETE":
            self.store.delete_tag(int(m.group(1)))
            return _json_bytes({"tags": self.store.list_tags()})

        return _json_bytes({"error": "Not found"}, 404)

    def _static(self, rel: str) -> tuple[int, dict[str, str], bytes]:
        rel = posixpath.normpath(rel).lstrip("/")
        if rel.startswith(".."):
            return _json_bytes({"error": "Not found"}, 404)
        path = static_dir() / rel
        if not path.is_file():
            return _json_bytes({"error": "Not found"}, 404)
        data = path.read_bytes()
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if path.suffix == ".js":
            ctype = "text/javascript; charset=utf-8"
        elif path.suffix == ".css":
            ctype = "text/css; charset=utf-8"
        elif path.suffix == ".html":
            ctype = "text/html; charset=utf-8"
        return 200, {"Content-Type": ctype, "Cache-Control": "no-store"}, data

    def _body_json(self, body: bytes) -> dict[str, Any]:
        if not body:
            return {}
        try:
            data = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("Expected a JSON object")
        return data

    def _list_emails(self, qs: dict[str, list[str]]) -> tuple[int, dict[str, str], bytes]:
        def one(name: str, default: str = "") -> str:
            return qs.get(name, [default])[0]

        limit = min(max(int(one("limit", "100") or 100), 1), 500)
        offset = max(int(one("offset", "0") or 0), 0)
        year_raw = one("year")
        year = int(year_raw) if year_raw else None
        result = search(
            self.store,
            q=one("q"),
            folder=one("folder") or None,
            tag=one("tag") or None,
            starred=_query_flag(qs, "starred"),
            unread=None,
            has_attachments=_query_flag(qs, "has_attachments"),
            year=year,
            sort=one("sort", "date_desc"),
            limit=limit,
            offset=offset,
        )
        return _json_bytes(result)

    def _get_email(self, email_id: int) -> tuple[int, dict[str, str], bytes]:
        rec = self.store.get_email(email_id)
        if not rec:
            return _json_bytes({"error": "Unknown email"}, 404)
        path = Path(rec["path"])
        rec["missing"] = not path.is_file()
        rec["body_text"] = rec.get("body_text") or ""
        if rec["missing"]:
            root = self.store.archive_root()
            try:
                drive_ok = bool(root) and Path(root).exists()
            except OSError:
                drive_ok = False
            if drive_ok:
                self.store.delete_email(email_id)
                return _json_bytes(
                    {"error": "This .eml file was deleted or moved and has been removed from the index", "removed": True},
                    404,
                )
        return _json_bytes(rec)

    def _patch_email(self, email_id: int, body: bytes) -> tuple[int, dict[str, str], bytes]:
        rec = self.store.get_email(email_id)
        if not rec:
            return _json_bytes({"error": "Unknown email"}, 404)
        payload = self._body_json(body)
        if "starred" in payload:
            self.store.set_starred(email_id, bool(payload["starred"]))
        if "note" in payload:
            self.store.set_note(email_id, str(payload["note"]))
        if "tag_id" in payload and "tagged" in payload:
            self.store.tag_email(email_id, int(payload["tag_id"]), bool(payload["tagged"]))
        if "tag_name" in payload:
            tag_id = self.store.ensure_tag(str(payload["tag_name"]))
            self.store.tag_email(email_id, tag_id, True)
        return self._get_email(email_id)

    def _email_html(self, email_id: int, qs: dict[str, list[str]]) -> tuple[int, dict[str, str], bytes]:
        rec = self.store.get_email(email_id)
        if not rec:
            return _json_bytes({"error": "Unknown email"}, 404)
        path = Path(rec["path"])
        if not path.is_file():
            html = text_as_html(
                "The original .eml file is missing or not reachable from this computer.\n\n"
                f"{path}\n\n"
                "A full reindex is not required. Check that the drive is mounted, then open the message again."
            )
            return 200, {"Content-Type": "text/html; charset=utf-8"}, html.encode("utf-8")
        try:
            msg = load_message(path)
        except (OSError, ParseError, ValueError) as exc:
            html = text_as_html(
                "Could not extract this message from the original .eml file.\n\n"
                f"{path}\n\n{exc}\n\n"
                "A full reindex is not required. Click Repair blank bodies to refill empty index rows only."
            )
            return 200, {"Content-Type": "text/html; charset=utf-8"}, html.encode("utf-8", errors="replace")
        allow_remote = _query_flag(qs, "remote") is True
        html_body = get_html_body(msg)
        text_body = get_text_body(msg)
        cid_prefix = f"/api/emails/{email_id}/cid"
        doc, blocked = build_view_document(
            html_body,
            text_body,
            rec.get("body_text") or "",
            allow_remote=allow_remote,
            cid_prefix=cid_prefix,
            title=rec.get("subject") or "Message",
        )
        headers = {
            "Content-Type": "text/html; charset=utf-8",
            "X-Blocked-Remote-Images": str(blocked),
            "Content-Security-Policy": "default-src 'none'; img-src data: blob: http: https: 'self'; style-src 'unsafe-inline'; frame-ancestors 'self'; base-uri 'none'",
        }
        return 200, headers, doc.encode("utf-8", errors="replace")

    def _reveal_email(self, email_id: int) -> tuple[int, dict[str, str], bytes]:
        rec = self.store.get_email(email_id)
        if not rec:
            return _json_bytes({"error": "Unknown email"}, 404)
        path = Path(rec["path"])
        if not path.exists():
            return _json_bytes({"error": "Original file is not reachable"}, 404)
        cmd = reveal_command(path)
        try:
            subprocess.Popen(
                cmd,
                close_fds=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            return _json_bytes({"error": f"Could not open folder: {exc}"}, 500)
        return _json_bytes({"ok": True, "path": str(path)})

    def _attachment(self, email_id: int, part_index: int) -> tuple[int, dict[str, str], bytes]:
        rec = self.store.get_email(email_id)
        if not rec:
            return _json_bytes({"error": "Unknown email"}, 404)
        path = Path(rec["path"])
        if not path.is_file():
            return _json_bytes({"error": "Original file is not reachable"}, 404)
        try:
            msg = load_message(path)
        except (OSError, ParseError, ValueError) as exc:
            return _json_bytes({"error": str(exc)}, 500)
        part = find_part_by_index(msg, part_index)
        if part is None:
            return _json_bytes({"error": "Attachment not found"}, 404)
        payload = decode_part_bytes(part)
        filename = part.get_filename() or f"attachment-{part_index}"
        ctype = part.get_content_type() or "application/octet-stream"
        headers = {
            "Content-Type": ctype,
            "Content-Disposition": f'attachment; filename="{filename}"',
        }
        return 200, headers, payload

    def _cid(self, email_id: int, content_id: str) -> tuple[int, dict[str, str], bytes]:
        rec = self.store.get_email(email_id)
        if not rec:
            return _json_bytes({"error": "Unknown email"}, 404)
        path = Path(rec["path"])
        if not path.is_file():
            return 404, {"Content-Type": "text/plain"}, b"missing"
        try:
            msg = load_message(path)
        except (OSError, ParseError, ValueError):
            return 404, {"Content-Type": "text/plain"}, b"missing"
        part = find_part_by_cid(msg, content_id)
        if part is None:
            return 404, {"Content-Type": "text/plain"}, b"missing"
        payload = decode_part_bytes(part)
        ctype = part.get_content_type() or "application/octet-stream"
        return 200, {"Content-Type": ctype, "Cache-Control": "private, max-age=3600"}, payload

    def _start_index(self, body: bytes) -> tuple[int, dict[str, str], bytes]:
        payload = self._body_json(body)
        root = payload.get("root") or self.store.archive_root()
        repair_empty = bool(payload.get("repair_empty"))
        full = bool(payload.get("full"))
        if repair_empty:
            root_path = Path(str(root)).expanduser() if root else Path(".")
            if root and root_path.exists():
                self.store.set_archive_root(str(root_path.resolve()))
            started = self.indexer.start(root_path, full=False, repair_empty=True)
            snap = self.indexer.snapshot()
            snap["accepted"] = started
            return _json_bytes(snap, 202 if started else 200)
        if not root:
            raise ValueError("Choose a folder that contains the .eml files")
        root_path = Path(str(root)).expanduser()
        if not root_path.exists():
            raise ValueError(f"Folder not found: {root_path}")
        self.store.set_archive_root(str(root_path.resolve()))
        started = self.indexer.start(root_path, full=full)
        snap = self.indexer.snapshot()
        snap["accepted"] = started
        return _json_bytes(snap, 202 if started else 200)

    def _save_settings(self, body: bytes) -> tuple[int, dict[str, str], bytes]:
        payload = self._body_json(body)
        copied = False
        if payload.get("db_path"):
            copied = self._move_index(
                str(payload["db_path"]),
                copy_existing=bool(payload.get("copy_existing", True)),
            )
        if "archive_root" in payload:
            raw = str(payload["archive_root"]).strip()
            if raw:
                root = Path(raw).expanduser()
                if not root.exists():
                    raise ValueError(f"Folder not found: {root}")
                self.store.set_archive_root(str(root.resolve()))
            else:
                self.store.set_archive_root("")
        stats = self.store.stats()
        stats["index"] = self.indexer.snapshot()
        stats["copied"] = copied
        return _json_bytes(stats)

    def _move_index(self, raw: str, copy_existing: bool) -> bool:
        dest = resolve_db_path(raw)
        src = Path(self.store.db_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        copied = False
        same = src.exists() and dest.exists() and src.resolve() == dest.resolve()
        if not same:
            self.indexer.cancel()
            if copy_existing and src.is_file() and not dest.exists():
                self.store.checkpoint()
                self.store.close()
                copy_index_file(src, dest)
                copied = True
            else:
                self.store.close()
            self.store = Store(dest)
            self.indexer = Indexer(self.store)
        remember_db_path(Path(self.store.db_path))
        return copied

    def _list_fs(self, qs: dict[str, list[str]]) -> tuple[int, dict[str, str], bytes]:
        raw = qs.get("path", [""])[0]
        if not raw:
            home = Path.home()
            entries = [
                {"name": "Home", "path": str(home), "dir": True},
            ]
            if os.name == "nt":
                import string

                for letter in string.ascii_uppercase:
                    drive = Path(f"{letter}:\\")
                    try:
                        if drive.exists():
                            entries.append({"name": f"{letter}:\\", "path": str(drive), "dir": True})
                    except OSError:
                        continue
            for candidate in (Path("/mnt"), Path("/media"), Path("/Volumes"), Path("/")):
                if candidate.exists() and candidate.is_dir():
                    entries.append({"name": str(candidate), "path": str(candidate), "dir": True})
            return _json_bytes({"path": "", "parent": None, "entries": entries})
        path = Path(raw).expanduser()
        if not path.exists() or not path.is_dir():
            raise ValueError(f"Folder not found: {path}")
        entries: list[dict[str, Any]] = []
        try:
            children = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError as exc:
            raise ValueError(str(exc)) from exc
        for child in children[:500]:
            if child.name.startswith("."):
                continue
            try:
                is_dir = child.is_dir()
            except OSError:
                continue
            if not is_dir:
                if child.suffix.lower() not in {".eml", ".emlx"}:
                    continue
            entries.append({"name": child.name, "path": str(child), "dir": is_dir})
        parent = str(path.parent) if path.parent != path else None
        return _json_bytes({"path": str(path), "parent": parent, "entries": entries})


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:
            if getattr(self.server, "quiet", False):
                return
            try:
                super().log_message(fmt, *args)
            except Exception:
                return

        def _read_body(self) -> bytes:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                return b""
            return self.rfile.read(length)

        def _send(self, status: int, headers: dict[str, str], data: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Length", str(len(data)))
            for key, value in headers.items():
                self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        def _dispatch(self, method: str, body: bytes) -> None:
            try:
                parsed = urlparse(self.path)
                qs = parse_qs(parsed.query)
                status, headers, data = app.handle(method, parsed.path, qs, body)
                self._send(status, headers, data)
            except BrokenPipeError:
                return
            except Exception:
                try:
                    status, headers, data = _json_bytes({"error": "Internal error"}, 500)
                    self._send(status, headers, data)
                except Exception:
                    return

        def do_GET(self) -> None:  # noqa: N802
            self._dispatch("GET", b"")

        def do_POST(self) -> None:  # noqa: N802
            self._dispatch("POST", self._read_body())

        def do_DELETE(self) -> None:  # noqa: N802
            self._dispatch("DELETE", b"")

    return Handler


def health_ok(host: str, port: int) -> bool:
    if port <= 0:
        return False
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/api/health", timeout=0.6) as resp:
            return 200 <= getattr(resp, "status", 200) < 300
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return False


def _bind(handler, host: str, port: int) -> ThreadingHTTPServer:
    if port == 0:
        return ThreadingHTTPServer((host, 0), handler)
    last_error: OSError | None = None
    for candidate in range(port, port + 16):
        if health_ok(host, candidate):
            raise ExistingInstance(candidate)
        try:
            return ThreadingHTTPServer((host, candidate), handler)
        except OSError as exc:
            last_error = exc
            continue
    if last_error is not None:
        raise last_error
    raise OSError(f"Could not bind {host}:{port}")


def serve(
    app: App,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
    quiet: bool = False,
) -> ThreadingHTTPServer | None:
    if port and health_ok(host, port):
        if open_browser:
            import webbrowser

            webbrowser.open(f"http://{host}:{port}/")
        return None
    handler = make_handler(app)
    try:
        httpd = _bind(handler, host, port)
    except ExistingInstance as exc:
        if open_browser:
            import webbrowser

            webbrowser.open(f"http://{host}:{exc.port}/")
        return None
    httpd.quiet = quiet  # type: ignore[attr-defined]
    if open_browser:
        import webbrowser

        def _open() -> None:
            webbrowser.open(f"http://{host}:{httpd.server_port}/")

        threading.Timer(0.4, _open).start()
    return httpd
