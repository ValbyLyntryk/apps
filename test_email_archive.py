#!/usr/bin/env python3
"""Tests for the local EML archive viewer (no network)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.client import HTTPConnection
from pathlib import Path

from eml_archive.demo import write_demo_archive
from eml_archive.indexer import Indexer
from eml_archive.parser import parse_eml_bytes, parse_eml_file
from eml_archive.paths import is_frozen, package_root, static_dir
from eml_archive.sanitize import sanitize_html
from eml_archive.search import parse_query, search
from eml_archive.server import App, serve
from eml_archive.store import Store


class ParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = write_demo_archive(Path(self.tmp.name) / "mail")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_invoice_has_attachment_and_html(self) -> None:
        rec = parse_eml_file(self.root / "Invoices" / "2024" / "invoice-1042.eml")
        self.assertEqual(rec["subject"], "Invoice 1042 — paper delivery")
        self.assertIn("billing@acme.example", rec["sender_email"])
        self.assertEqual(rec["has_attachments"], 1)
        self.assertEqual(rec["has_html"], 1)
        self.assertIn("4.250,00 kr", rec["body_text"])
        names = [a["filename"] for a in rec["attachments"]]
        self.assertIn("invoice-1042.txt", names)

    def test_danish_plain_text(self) -> None:
        rec = parse_eml_file(self.root / "Personal" / "family-dinner.eml")
        self.assertIn("øl", rec["body_text"])
        self.assertEqual(rec["has_attachments"], 0)


class SanitizeTests(unittest.TestCase):
    def test_strips_script_and_blocks_remote_images(self) -> None:
        html, blocked = sanitize_html(
            "<p>Hi<script>alert(1)</script></p><img src='https://evil.example/x.gif'>",
            allow_remote=False,
            cid_prefix="/api/emails/1/cid",
        )
        self.assertNotIn("script", html.lower())
        self.assertNotIn("alert", html)
        self.assertGreaterEqual(blocked, 1)
        self.assertIn("data:image/svg+xml", html)

    def test_rewrites_cid_and_keeps_mailto(self) -> None:
        html, _blocked = sanitize_html(
            '<a href="mailto:a@b.c">x</a><img src="cid:pic@mail">',
            allow_remote=True,
            cid_prefix="/api/emails/9/cid",
        )
        self.assertIn("mailto:a@b.c", html)
        self.assertIn("/api/emails/9/cid/pic%40mail", html)

    def test_unclosed_style_does_not_blank_body(self) -> None:
        html, _blocked = sanitize_html(
            "<html><head><style>p{color:#fff}\n<p>Invoice 88 is attached</p>",
            allow_remote=False,
            cid_prefix="/c",
        )
        self.assertIn("Invoice 88 is attached", html)

    def test_white_text_stays_readable(self) -> None:
        html, _blocked = sanitize_html(
            '<p style="color:#ffffff;background:#000000">Secret body</p>',
            allow_remote=False,
            cid_prefix="/c",
        )
        self.assertIn("Secret body", html)
        self.assertNotIn("color:#ffffff", html.lower())


class BodyExtractionTests(unittest.TestCase):
    def test_html_marked_as_attachment_is_still_body(self) -> None:
        raw = (
            b"From: a@example.com\nTo: b@example.com\nSubject: Winmail style\n"
            b"MIME-Version: 1.0\n"
            b'Content-Type: text/html; charset="utf-8"\n'
            b'Content-Disposition: attachment; filename="message.html"\n\n'
            b"<p>Please confirm the order today.</p>\n"
        )
        rec = parse_eml_bytes(raw)
        self.assertIn("Please confirm the order today.", rec["body_text"])
        self.assertEqual(rec["has_html"], 1)

    def test_utf16_eml_roundtrip(self) -> None:
        text = (
            "From: a@example.com\r\nTo: b@example.com\r\nSubject: unicode dump\r\n"
            "Content-Type: text/plain; charset=utf-8\r\n\r\nHej Valby, her er oel.\r\n"
        )
        rec = parse_eml_bytes(text.encode("utf-16"))
        self.assertIn("Hej Valby", rec["body_text"])

    def test_view_falls_back_when_html_sanitizes_empty(self) -> None:
        from eml_archive.sanitize import build_view_document

        doc, _blocked = build_view_document(
            "<style>everything",
            "Plain text still here",
            "",
            allow_remote=False,
            cid_prefix="/c",
        )
        self.assertIn("Plain text still here", doc)


class QueryTests(unittest.TestCase):
    def test_operators(self) -> None:
        parsed = parse_query('from:Alice subject:"tax bill" after:2020-01-01 invoice')
        self.assertEqual(parsed["from"], ["Alice"])
        self.assertEqual(parsed["subject"], ["tax bill"])
        self.assertEqual(parsed["terms"], ["invoice"])
        self.assertIsNotNone(parsed["after"])


class IndexSearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = write_demo_archive(Path(self.tmp.name) / "mail")
        self.store = Store(Path(self.tmp.name) / "index.db")
        indexer = Indexer(self.store)
        indexer.run(self.root, full=True)
        self.assertEqual(indexer.snapshot()["phase"], "done")
        self.assertEqual(indexer.snapshot()["updated"], 5)

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    def test_does_not_move_files(self) -> None:
        original = list(self.root.rglob("*.eml"))
        self.assertEqual(len(original), 5)
        for path in original:
            self.assertTrue(path.is_file())

    def test_search_invoice_and_folder(self) -> None:
        hit = search(self.store, q="invoice")
        self.assertGreaterEqual(hit["total"], 1)
        self.assertTrue(any("Invoice" in e["subject"] for e in hit["emails"]))
        folded = search(self.store, folder="Projects")
        self.assertEqual(folded["total"], 2)
        nested = search(self.store, folder="Invoices/2024")
        self.assertEqual(nested["total"], 1)

    def test_from_operator_and_sort(self) -> None:
        hit = search(self.store, q="from:mette")
        self.assertEqual(hit["total"], 1)
        newest = search(self.store, sort="date_desc")
        oldest = search(self.store, sort="date_asc")
        self.assertEqual(newest["emails"][0]["subject"], "These files stay on the drive")
        self.assertEqual(oldest["emails"][0]["subject"], "Sunday dinner — bring øl?")

    def test_incremental_skips_unchanged(self) -> None:
        indexer = Indexer(self.store)
        indexer.run(self.root, full=False)
        snap = indexer.snapshot()
        self.assertEqual(snap["skipped"], 5)
        self.assertEqual(snap["updated"], 0)

    def test_tags_stars_notes_stay_in_db(self) -> None:
        eml_path = (self.root / "Personal" / "family-dinner.eml").resolve()
        rec = self.store.conn.execute(
            "SELECT id, path FROM emails WHERE path = ?", (str(eml_path),)
        ).fetchone()
        email_id = rec["id"]
        self.store.set_starred(email_id, True)
        tag_id = self.store.ensure_tag("family")
        self.store.tag_email(email_id, tag_id, True)
        self.store.set_note(email_id, "bring salad too")
        tagged = search(self.store, tag="family")
        self.assertEqual(tagged["total"], 1)
        starred = search(self.store, starred=True)
        self.assertEqual(starred["total"], 1)
        detail = self.store.get_email(email_id)
        assert detail is not None
        self.assertEqual(detail["note"], "bring salad too")
        self.assertTrue((self.root / "Personal" / "family-dinner.eml").is_file())


class ServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = write_demo_archive(Path(self.tmp.name) / "mail")
        self.store = Store(Path(self.tmp.name) / "index.db")
        Indexer(self.store).run(self.root, full=True)
        self.app = App(self.store)
        self.httpd = serve(self.app, host="127.0.0.1", port=0, open_browser=False, quiet=True)
        self.assertIsNotNone(self.httpd)
        self.port = self.httpd.server_port
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        time.sleep(0.05)

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.store.close()
        self.tmp.cleanup()

    def _json(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        payload = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"} if payload else {}
        conn.request(method, path, body=payload, headers=headers)
        resp = conn.getresponse()
        raw = resp.read()
        conn.close()
        data = json.loads(raw.decode()) if raw else {}
        return resp.status, data

    def test_list_search_view_and_tag(self) -> None:
        status, stats = self._json("GET", "/api/stats")
        self.assertEqual(status, 200)
        self.assertEqual(stats["total"], 5)
        status, listing = self._json("GET", "/api/emails?q=catalogue")
        self.assertEqual(status, 200)
        self.assertEqual(listing["total"], 1)
        email_id = listing["emails"][0]["id"]
        status, detail = self._json("GET", f"/api/emails/{email_id}")
        self.assertEqual(status, 200)
        self.assertTrue(detail["path"].endswith("html-only.eml"))
        self.assertTrue(Path(detail["path"]).is_file())

        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", f"/api/emails/{email_id}/html")
        resp = conn.getresponse()
        html = resp.read().decode()
        conn.close()
        self.assertEqual(resp.status, 200)
        self.assertIn("January catalogue", html)
        self.assertNotIn("alert('xss')", html)
        self.assertNotIn("<script", html.lower())

        status, tagged = self._json(
            "POST", f"/api/emails/{email_id}", {"tag_name": "shop", "starred": True}
        )
        self.assertEqual(status, 200)
        self.assertEqual(tagged["starred"], 1)
        self.assertTrue(any(t["name"] == "shop" for t in tagged["tags"]))
        status, listing = self._json("GET", "/api/emails?tag=shop&starred=1")
        self.assertEqual(listing["total"], 1)

    def test_attachment_download(self) -> None:
        status, listing = self._json("GET", "/api/emails?q=from:billing")
        self.assertEqual(listing["total"], 1)
        email_id = listing["emails"][0]["id"]
        status, detail = self._json("GET", f"/api/emails/{email_id}")
        att = next(a for a in detail["attachments"] if a["filename"] == "invoice-1042.txt")
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", f"/api/emails/{email_id}/attachments/{att['part_index']}")
        resp = conn.getresponse()
        payload = resp.read()
        conn.close()
        self.assertEqual(resp.status, 200)
        self.assertIn(b"Invoice 1042", payload)

    def test_ui_is_served(self) -> None:
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", "/")
        resp = conn.getresponse()
        body = resp.read().decode()
        conn.close()
        self.assertEqual(resp.status, 200)
        self.assertIn("Email archive", body)
        self.assertIn("/static/app.js", body)

    def test_second_start_reuses_running_instance(self) -> None:
        extra = serve(self.app, host="127.0.0.1", port=self.port, open_browser=False, quiet=True)
        self.assertIsNone(extra)

    def test_settings_copy_index_to_new_folder(self) -> None:
        dest = Path(self.tmp.name) / "drive-y" / "Mails"
        status, stats = self._json(
            "POST",
            "/api/settings",
            {"db_path": str(dest), "copy_existing": True},
        )
        self.assertEqual(status, 200)
        self.assertTrue(stats["copied"])
        self.assertEqual(stats["total"], 5)
        self.assertTrue(str(stats["db_path"]).endswith("archive.db"))
        self.assertTrue(Path(stats["db_path"]).is_file())
        status, listing = self._json("GET", "/api/emails")
        self.assertEqual(listing["total"], 5)


class PathTests(unittest.TestCase):
    def test_source_tree_has_ui_files(self) -> None:
        self.assertFalse(is_frozen())
        self.assertTrue((static_dir() / "index.html").is_file())
        self.assertTrue((static_dir() / "app.js").is_file())
        self.assertEqual(package_root().name, "eml_archive")

    def test_frozen_looks_under_meipass(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        bundled = tmp / "eml_archive" / "static"
        bundled.mkdir(parents=True)
        (bundled / "index.html").write_text("<html></html>", encoding="utf-8")
        old_frozen = getattr(sys, "frozen", None)
        old_mei = getattr(sys, "_MEIPASS", None)
        try:
            sys.frozen = True  # type: ignore[attr-defined]
            sys._MEIPASS = str(tmp)  # type: ignore[attr-defined]
            self.assertTrue(is_frozen())
            self.assertEqual(package_root(), tmp / "eml_archive")
            self.assertTrue((static_dir() / "index.html").is_file())
        finally:
            if old_frozen is None:
                delattr(sys, "frozen")
            else:
                sys.frozen = old_frozen  # type: ignore[attr-defined]
            if old_mei is None:
                delattr(sys, "_MEIPASS")
            else:
                sys._MEIPASS = old_mei  # type: ignore[attr-defined]


class BuildScriptTests(unittest.TestCase):
    def test_build_script_help(self) -> None:
        root = Path(__file__).resolve().parent
        out = subprocess.check_output(
            [sys.executable, str(root / "build_exe.py"), "--help"],
            text=True,
        )
        self.assertIn("EmailArchive", out)
        self.assertTrue((root / "build_exe.bat").is_file())
        self.assertTrue((root / "requirements-build.txt").is_file())
        launcher = (root / "email_archive.py").read_text(encoding="utf-8")
        self.assertNotIn("import multiprocessing", launcher)
        self.assertNotIn("freeze_support", launcher)
        self.assertIn("static", (root / "build_exe.py").read_text(encoding="utf-8"))


class IndexLocationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.ptr = Path(self.tmp.name) / "pointer.txt"
        os.environ["EMAIL_ARCHIVE_POINTER"] = str(self.ptr)

    def tearDown(self) -> None:
        os.environ.pop("EMAIL_ARCHIVE_POINTER", None)
        os.environ.pop("EMAIL_ARCHIVE_DB", None)
        self.tmp.cleanup()

    def test_folder_resolves_to_archive_db(self) -> None:
        from eml_archive.store import preferred_db_path, resolve_db_path

        resolved = resolve_db_path(r"Y:\Mails")
        self.assertEqual(resolved.name, "archive.db")
        self.assertTrue(str(resolved).replace("\\", "/").endswith("Mails/archive.db") or resolved.parts[-2] == "Mails")
        remembered = preferred_db_path(r"Y:\Mails")
        self.assertEqual(remembered.name, "archive.db")
        self.assertEqual(preferred_db_path(None), remembered)

    def test_copy_keeps_indexed_mail(self) -> None:
        from eml_archive.store import copy_index_file

        root = write_demo_archive(Path(self.tmp.name) / "mail")
        src = Path(self.tmp.name) / "old" / "archive.db"
        dest = Path(self.tmp.name) / "YMails" / "archive.db"
        store = Store(src)
        Indexer(store).run(root, full=True)
        n = store.stats()["total"]
        store.checkpoint()
        store.close()
        copy_index_file(src, dest)
        moved = Store(dest)
        self.assertEqual(moved.stats()["total"], n)
        self.assertGreaterEqual(n, 5)
        moved.close()


class CrashLogTests(unittest.TestCase):
    def test_report_crash_writes_file(self) -> None:
        from eml_archive.crash import report_crash

        try:
            raise RuntimeError("boom-test-crash")
        except RuntimeError as exc:
            path = report_crash(exc, pause=False)
        self.assertIsNotNone(path)
        assert path is not None
        self.assertTrue(path.is_file())
        self.assertIn("boom-test-crash", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
