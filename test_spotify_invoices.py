#!/usr/bin/env python3
"""Parser, state, and email tests for spotify_invoices.py (no browser)."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import spotify_invoices as sp

SAMPLE_HTML = """
<html>
  <a href="/dk/account/order-history/">Order history</a>
  <div>
    15/09/2026  99,00 kr.
    <a href="/dk/account/order-history/receipt/abc123">View receipts</a>
  </div>
  <div>
    2026-08-15  99 kr
    <a href="https://www.spotify.com/dk/account/subscription/receipt/xyz789">Se kvittering</a>
  </div>
</html>
"""

SAMPLE_JSON = {
    "data": {
        "payments": [
            {
                "id": "pay-1",
                "date": "2026-09-15",
                "amount": {"amount": "99.00", "currency": "DKK"},
                "receiptUrl": "/dk/account/order-history/receipt/pay-1",
                "description": "Premium Individual",
            },
            {
                "id": "pay-old",
                "paymentDate": "2025-01-01",
                "total": "99 DKK",
            },
        ]
    }
}


class SpotifyInvoiceTests(unittest.TestCase):
    def test_parses_receipt_links_and_skips_listing(self) -> None:
        rows = sp.parse_receipts_from_html(SAMPLE_HTML)
        ids = [row["id"] for row in rows]
        self.assertTrue(any("abc123" in item for item in ids))
        self.assertTrue(any("xyz789" in item for item in ids))
        self.assertFalse(any(item.rstrip("/").endswith("order-history") for item in ids))
        abc = next(row for row in rows if "abc123" in row["id"])
        self.assertIn("15/09/2026", abc["date"] or "")
        self.assertIsNotNone(abc["amount"])

    def test_extracts_receipts_from_nested_json(self) -> None:
        rows = sp.extract_receipts_from_json(SAMPLE_JSON)
        by_id = {row["id"]: row for row in rows}
        self.assertIn("pay-1", by_id)
        self.assertEqual(by_id["pay-1"]["date"], "2026-09-15")
        self.assertIn("99.00", by_id["pay-1"]["amount"])
        self.assertIn("receipt/pay-1", by_id["pay-1"]["url"])
        self.assertIn("pay-old", by_id)

    def test_first_run_keeps_recent_and_undated(self) -> None:
        now = datetime(2026, 10, 7, tzinfo=timezone.utc)
        rows = [
            {"id": "new", "date": "2026-09-15"},
            {"id": "old", "date": "2025-01-01"},
            {"id": "mystery", "date": None},
        ]
        kept = {row["id"] for row in sp.filter_recent(rows, 45, now=now)}
        self.assertEqual(kept, {"new", "mystery"})

    def test_new_receipts_skips_already_sent(self) -> None:
        rows = [{"id": "a"}, {"id": "b"}, {"url": "https://x/c"}]
        fresh = sp.new_receipts(rows, sent_ids=["a"])
        self.assertEqual([sp.receipt_id(row) for row in fresh], ["b", "https://x/c"])

    def test_email_attaches_pdf_and_names_subject(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "spotify-receipt-2026-09-15.pdf"
            path.write_bytes(b"%PDF-test")
            rows = [{"id": "pay-1", "date": "2026-09-15", "amount": "99 DKK", "title": "Premium"}]
            message = sp.build_email("faktura@valbylyntryk.dk", rows, [path], "from@example.com")
            self.assertIn("1 stk", message["Subject"])
            self.assertIn("2026-09-15", message["Subject"])
            self.assertEqual(message["To"], "faktura@valbylyntryk.dk")
            body = message.get_body(preferencelist=("plain",)).get_content()
            self.assertIn("Premium", body)
            filenames = [
                part.get_filename()
                for part in message.iter_attachments()
            ]
            self.assertEqual(filenames, ["spotify-receipt-2026-09-15.pdf"])

    def test_smtp_falls_back_to_lomax_env(self) -> None:
        previous = {key: os.environ.get(key) for key in list(os.environ) if "SMTP" in key or "ALERT" in key}
        for key in list(os.environ):
            if key.startswith(("SPOTIFY_", "LOMAX_")):
                os.environ.pop(key, None)
        try:
            os.environ["LOMAX_SMTP_HOST"] = "smtp.gmail.com"
            os.environ["LOMAX_SMTP_USER"] = "extra@gmail.com"
            os.environ["LOMAX_ALERT_EMAIL"] = "faktura@valbylyntryk.dk"
            settings = sp.smtp_settings()
            self.assertEqual(settings["host"], "smtp.gmail.com")
            self.assertEqual(sp.alert_email(), "faktura@valbylyntryk.dk")
        finally:
            for key in list(os.environ):
                if key.startswith(("SPOTIFY_", "LOMAX_")):
                    os.environ.pop(key, None)
            for key, value in previous.items():
                if value is not None:
                    os.environ[key] = value

    def test_login_url_detection(self) -> None:
        self.assertTrue(sp.looks_like_login("https://accounts.spotify.com/da/login"))
        self.assertFalse(sp.looks_like_login("https://www.spotify.com/dk/account/overview/"))

    def test_safe_filename_is_boring(self) -> None:
        name = sp.safe_filename({"id": "pay/1", "date": "2026-09-15", "amount": "99,00 kr"})
        self.assertTrue(name.startswith("spotify-receipt-2026-09-15-"))
        self.assertTrue(name.endswith(".pdf"))
        self.assertNotIn("/", name)


if __name__ == "__main__":
    unittest.main()
