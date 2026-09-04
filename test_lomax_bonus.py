#!/usr/bin/env python3
"""Parser and ranking tests for lomax_bonus.py (no network)."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import lomax_bonus as lb

SAMPLE_100 = """
<div class="product-list-item row mb-3 py-3" data-cnstrc-item-id="70132800" data-cnstrc-item-name="Twincase iPhone 13 Pro case, transparent" data-cnstrc-item-price="23.75">
<div class="product-labels">
        <span class="badge text-bg-remnant-sale ">Restsalg</span>
            <div class="badge badge-bonus ">
                <span class="text-bg-bonus">100%</span>
                <span class="text-bg-light">Bonus</span>
            </div>
</div>
                    <a href="/elektronik/covers/twincase-iphone-13-pro-case-transparent-70132800/">
                    <h5 class="product-name">Twincase iPhone 13 Pro case, transparent</h5>
                    <p class="product-description d-none d-lg-block text-muted mb-2">Elegant beskyttelse</p>
                <small class="text-muted">Varenr 70132800</small>
                        <div class="text-right small">
                            <del>F&#xF8;r: 123,75 kr.</del>
                        </div>
</div>
"""

SAMPLE_5 = """
<div class="product-list-item row mb-3 py-3" data-cnstrc-item-id="1515200" data-cnstrc-item-name="Lomax kopipapir" data-cnstrc-item-price="49.94">
            <div class="badge badge-bonus ">
                <span class="text-bg-light">5%</span>
                <span class="text-bg-light">Bonus</span>
            </div>
                    <a href="/kontorartikler/papir/lomax-kopipapir-1515200/">
                    <h5 class="product-name">Lomax kopipapir</h5>
                <small class="text-muted">Varenr 1515200</small>
</div>
"""

SAMPLE_PAGE = SAMPLE_100 + SAMPLE_5 + """
                    <div class="search-pagination-dropdown dropdown"
                        data-current-page="1"
                        data-total-pages="209">
                    </div>
"""


class ParseTests(unittest.TestCase):
    def test_parses_elevated_bonus_and_was_price(self) -> None:
        product = lb.parse_product(SAMPLE_100, page=1, base="https://www.lomax.dk/soeg/")
        assert product is not None
        self.assertEqual(product["varenr"], "70132800")
        self.assertEqual(product["bonus_pct"], 100)
        self.assertEqual(product["price"], 23.75)
        self.assertEqual(product["was_price"], 123.75)
        self.assertEqual(product["discount_pct"], 81)
        self.assertIn("Restsalg", product["badges"])
        self.assertTrue(product["url"].endswith("70132800/"))

    def test_parses_baseline_five_percent(self) -> None:
        product = lb.parse_product(SAMPLE_5, page=1, base="https://www.lomax.dk/soeg/")
        assert product is not None
        self.assertEqual(product["bonus_pct"], 5)

    def test_detects_page_count_and_chunks(self) -> None:
        self.assertEqual(lb.detect_total_pages(SAMPLE_PAGE), 209)
        self.assertEqual(len(lb.product_chunks(SAMPLE_PAGE)), 2)

    def test_ranking_and_min_bonus_filter(self) -> None:
        low = lb.parse_product(SAMPLE_5, 1, "https://www.lomax.dk/soeg/")
        high = lb.parse_product(SAMPLE_100, 1, "https://www.lomax.dk/soeg/")
        ranked = lb.rank([low, high])  # type: ignore[list-item]
        self.assertEqual(ranked[0]["bonus_pct"], 100)
        self.assertEqual(len(lb.filter_min_bonus(ranked, 25)), 1)

    def test_compare_detects_new_changed_and_gone(self) -> None:
        old = [
            {"varenr": "1", "bonus_pct": 50, "name": "Keep", "page": 1, "price": 10},
            {"varenr": "2", "bonus_pct": 75, "name": "Drop", "page": 1, "price": 10},
            {"varenr": "3", "bonus_pct": 50, "name": "Raise", "page": 1, "price": 10},
        ]
        new = [
            {"varenr": "1", "bonus_pct": 50, "name": "Keep", "page": 1, "price": 10},
            {"varenr": "3", "bonus_pct": 100, "name": "Raise", "page": 1, "price": 10},
            {"varenr": "4", "bonus_pct": 100, "name": "Fresh", "page": 1, "price": 10},
        ]
        diff = lb.compare_runs(old, new, minimum=25)
        self.assertEqual([row["varenr"] for row in diff["appeared"]], ["4"])
        self.assertEqual([row["varenr"] for row in diff["disappeared"]], ["2"])
        self.assertEqual(diff["changed"][0]["varenr"], "3")
        self.assertEqual(diff["changed"][0]["previous_bonus_pct"], 50)

    def test_listing_url_sets_hits_and_page(self) -> None:
        url = lb.listing_url("https://www.lomax.dk/soeg/?hits=48", hits=48, page=7)
        self.assertIn("page=7", url)
        self.assertIn("hits=48", url)

    def test_first_run_is_silent_baseline(self) -> None:
        current = [
            {"varenr": "1", "bonus_pct": 100, "name": "Case", "page": 1, "price": 20},
            {"varenr": "2", "bonus_pct": 75, "name": "Lamp", "page": 1, "price": 20},
        ]
        self.assertEqual(lb.alert_candidates(None, current, min_bonus=75), [])

    def test_alerts_on_new_and_upgraded_high_bonuses(self) -> None:
        previous = [
            {"varenr": "keep", "bonus_pct": 100, "name": "Keep", "page": 1, "price": 10},
            {"varenr": "raise", "bonus_pct": 75, "name": "Raise", "page": 1, "price": 10},
            {"varenr": "upgrade", "bonus_pct": 25, "name": "Upgrade", "page": 1, "price": 10},
            {"varenr": "ignore", "bonus_pct": 50, "name": "Ignore", "page": 1, "price": 10},
        ]
        current = [
            {"varenr": "keep", "bonus_pct": 100, "name": "Keep", "page": 1, "price": 10},
            {"varenr": "raise", "bonus_pct": 100, "name": "Raise", "page": 1, "price": 10},
            {"varenr": "upgrade", "bonus_pct": 75, "name": "Upgrade", "page": 1, "price": 10},
            {"varenr": "fresh", "bonus_pct": 100, "name": "Fresh", "page": 1, "price": 10},
            {"varenr": "ignore", "bonus_pct": 50, "name": "Ignore", "page": 1, "price": 10},
        ]
        alerts = lb.alert_candidates(previous, current, min_bonus=75)
        by_id = {row["varenr"]: row for row in alerts}
        self.assertEqual(set(by_id), {"raise", "upgrade", "fresh"})
        self.assertEqual(by_id["fresh"]["alert_reason"], "new")
        self.assertEqual(by_id["upgrade"]["alert_reason"], "upgraded")
        self.assertEqual(by_id["raise"]["alert_reason"], "raised")
        self.assertNotIn("keep", by_id)
        self.assertNotIn("ignore", by_id)

    def test_alert_copy_mentions_counts(self) -> None:
        alerts = [
            {"varenr": "1", "bonus_pct": 100, "name": "Case", "price": 23.75, "url": "https://x"},
            {"varenr": "2", "bonus_pct": 75, "name": "Lamp", "price": 100, "url": "https://y"},
        ]
        title = lb.format_alert_title(alerts)
        self.assertIn("100%", title)
        self.assertIn("75%", title)
        text = lb.format_alert_text(alerts)
        self.assertIn("Case", text)
        self.assertIn("https://x", text)

    def test_env_file_loads_missing_keys_only(self) -> None:
        os.environ.pop("LOMAX_ALERT_EMAIL", None)
        previous_host = os.environ.get("LOMAX_SMTP_HOST")
        os.environ["LOMAX_SMTP_HOST"] = "already.example"
        try:
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "lomax-bonus.env"
                path.write_text(
                    "LOMAX_ALERT_EMAIL=extra@example.com\n"
                    "LOMAX_SMTP_HOST=smtp.gmail.com\n"
                    "LOMAX_SMTP_PASSWORD=abcd efgh ijkl mnop\n",
                    encoding="utf-8",
                )
                loaded = lb.load_env_file(path)
            self.assertEqual(loaded["LOMAX_ALERT_EMAIL"], "extra@example.com")
            self.assertNotIn("LOMAX_SMTP_HOST", loaded)
            self.assertEqual(os.environ["LOMAX_SMTP_HOST"], "already.example")
            self.assertEqual(os.environ["LOMAX_ALERT_EMAIL"], "extra@example.com")
        finally:
            os.environ.pop("LOMAX_ALERT_EMAIL", None)
            if previous_host is None:
                os.environ.pop("LOMAX_SMTP_HOST", None)
            else:
                os.environ["LOMAX_SMTP_HOST"] = previous_host

    def test_test_email_title_is_obvious(self) -> None:
        title = lb.format_alert_title([lb.TEST_ALERT])
        self.assertIn("test email", title.lower())


if __name__ == "__main__":
    unittest.main()
