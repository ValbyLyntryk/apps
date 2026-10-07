#!/usr/bin/env python3
"""Download Spotify receipts from a saved browser login and email new ones.

Spotify has no invoice API. This script opens the account pages with a
saved Playwright session, grabs receipt PDFs, and mails the new ones.

    python3 spotify_invoices.py --login
    python3 spotify_invoices.py --send-test-email
    python3 spotify_invoices.py
    python3 spotify_invoices.py --dry-run --headed

Never put your Spotify password in this repo, an env file, or a chat.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

DEFAULT_ENV_FILE = "spotify-invoices.env"
DEFAULT_STORAGE = "spotify-storage.json"
DEFAULT_STATE = "spotify-invoices-state.json"
DEFAULT_RECEIPT_DIR = "spotify-receipts"
DEFAULT_DAYS = 45
DEFAULT_ACCOUNT = "https://www.spotify.com/dk/account/overview/"
HISTORY_URLS = (
    "https://www.spotify.com/dk/account/order-history/",
    "https://www.spotify.com/dk/account/payment-history/",
    "https://www.spotify.com/account/order-history/",
    "https://www.spotify.com/account/subscription/receipt/",
)
LOGIN_HINTS = ("/login", "/signup", "accounts.spotify.com", "password")
RECEIPT_HREF_RE = re.compile(
    r'href="([^"]*(?:receipt|kvittering)[^"]*|[^"]*order-history/[^"/][^"]*)"',
    re.IGNORECASE,
)
DATE_RE = re.compile(
    r"\b(\d{4}-\d{2}-\d{2}|\d{1,2}[./]\d{1,2}[./]\d{2,4})\b"
)
AMOUNT_RE = re.compile(
    r"((?:DKK|kr\.?|EUR|USD|£|€)\s*[0-9][0-9.,]*|[0-9][0-9.,]*\s*(?:DKK|kr\.?|EUR|USD))",
    re.IGNORECASE,
)
JSON_URL_HINTS = (
    "receipt",
    "payment",
    "invoice",
    "order",
    "billing",
    "history",
    "transaction",
    "charge",
)
CLICK_LABELS = (
    "View receipts",
    "Se kvitteringer",
    "Se kvittering",
    "More details",
    "Flere oplysninger",
    "Download",
    "Hent",
    "Manage",
    "Administrer",
)
HISTORY_LINK_LABELS = (
    "Order history",
    "Ordrehistorik",
    "Payment history",
    "Betalingshistorik",
    "Your payments",
    "Dine betalinger",
)


def unescape(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def load_env_file(path: Path) -> dict[str, str]:
    loaded: dict[str, str] = {}
    if not path.exists():
        return loaded
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if not key or key in os.environ:
            continue
        os.environ[key] = value
        loaded[key] = value
    return loaded


def default_env_paths() -> list[Path]:
    here = Path(__file__).resolve().parent
    return [
        Path.cwd() / DEFAULT_ENV_FILE,
        here / DEFAULT_ENV_FILE,
        Path.cwd() / "lomax-bonus.env",
        here / "lomax-bonus.env",
    ]


def env_get(*names: str, default: str = "") -> str:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return default


def smtp_settings() -> dict[str, str]:
    return {
        "host": env_get("SPOTIFY_SMTP_HOST", "LOMAX_SMTP_HOST"),
        "port": env_get("SPOTIFY_SMTP_PORT", "LOMAX_SMTP_PORT", default="587"),
        "user": env_get("SPOTIFY_SMTP_USER", "LOMAX_SMTP_USER"),
        "password": env_get("SPOTIFY_SMTP_PASSWORD", "LOMAX_SMTP_PASSWORD").replace(" ", ""),
        "from_addr": env_get("SPOTIFY_SMTP_FROM", "LOMAX_SMTP_FROM"),
    }


def alert_email(explicit: str | None = None) -> str:
    return (explicit or env_get("SPOTIFY_ALERT_EMAIL", "LOMAX_ALERT_EMAIL")).strip()


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"sent_ids": [], "sent": []}
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if "sent_ids" not in data:
        data["sent_ids"] = [row.get("id") for row in data.get("sent") or [] if row.get("id")]
    return data


def save_state(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%d/%m/%y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text[:19], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    match = DATE_RE.search(text)
    if match and match.group(1) != text:
        return parse_date(match.group(1))
    return None


def receipt_id(row: dict[str, Any]) -> str:
    for key in ("id", "receipt_id", "url"):
        value = row.get(key)
        if value:
            return str(value)
    bits = [str(row.get("date") or ""), str(row.get("amount") or ""), str(row.get("title") or "")]
    return "|".join(bits).strip("|") or "unknown"


def safe_filename(row: dict[str, Any], suffix: str = ".pdf") -> str:
    date = (row.get("date") or "unknown-date").replace("/", "-")
    amount = re.sub(r"[^0-9A-Za-z]+", "", str(row.get("amount") or "receipt"))
    ident = re.sub(r"[^0-9A-Za-z_-]+", "", receipt_id(row))[:24] or "item"
    return f"spotify-receipt-{date}-{amount}-{ident}{suffix}"


def absolute_url(href: str, base: str = DEFAULT_ACCOUNT) -> str:
    return urljoin(base, href)


def looks_like_login(url: str) -> bool:
    low = url.lower()
    return any(hint in low for hint in LOGIN_HINTS) and "/account/" not in low


def parse_receipts_from_html(html: str, base: str = DEFAULT_ACCOUNT) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    for match in RECEIPT_HREF_RE.finditer(html):
        href = match.group(1).replace("&amp;", "&")
        url = absolute_url(href, base)
        path = url.split("?", 1)[0].rstrip("/")
        if path.endswith(("/receipt", "/order-history", "/payment-history")):
            continue
        ident = url
        if ident in seen:
            continue
        seen.add(ident)
        start = max(0, match.start() - 400)
        snippet = unescape(re.sub(r"<[^>]+>", " ", html[start : match.end() + 200]))
        date_match = DATE_RE.search(snippet)
        amount_match = AMOUNT_RE.search(snippet)
        found.append(
            {
                "id": ident,
                "url": url,
                "date": date_match.group(1) if date_match else None,
                "amount": amount_match.group(1).strip() if amount_match else None,
                "title": snippet[:120],
                "source": "html",
            }
        )
    return found


def _json_blob_to_receipt(blob: dict[str, Any], base: str) -> dict[str, Any] | None:
    keys = {key.lower(): key for key in blob}
    url = None
    for name in (
        "receipturl",
        "receipt_url",
        "invoiceurl",
        "downloadurl",
        "pdfurl",
        "url",
        "href",
    ):
        raw = blob.get(keys[name]) if name in keys else None
        if isinstance(raw, str) and raw.startswith(("http", "/")):
            url = absolute_url(raw, base)
            break
    date = None
    for name in ("date", "paymentdate", "chargedate", "createdat", "formatteddate"):
        raw = blob.get(keys[name]) if name in keys else None
        if raw:
            date = str(raw)[:10]
            break
    amount = None
    for name in ("amount", "total", "price", "cost", "formattedamount"):
        raw = blob.get(keys[name]) if name in keys else None
        if isinstance(raw, dict):
            amount = str(raw.get("amount") or raw.get("value") or raw.get("formatted") or "")
            currency = raw.get("currency") or raw.get("currencyCode") or ""
            amount = f"{amount} {currency}".strip() or None
        elif raw not in (None, ""):
            amount = str(raw)
        if amount:
            break
    ident = None
    for name in ("id", "receiptid", "receipt_id"):
        if name in keys and blob.get(keys[name]):
            ident = blob.get(keys[name])
            break
    ident = ident or url
    if not ident and not date and not amount:
        return None
    if not ident:
        ident = "|".join(bit for bit in (date, amount) if bit) or None
    if not ident:
        return None
    title = "Spotify receipt"
    for name in ("description", "title", "name"):
        if name in keys and blob.get(keys[name]):
            title = str(blob.get(keys[name]))
            break
    return {
        "id": str(ident),
        "url": url,
        "date": date,
        "amount": amount,
        "title": title,
        "source": "json",
    }


def extract_receipts_from_json(payload: Any, base: str = DEFAULT_ACCOUNT) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            low = {str(key).lower() for key in node}
            interesting = low & {
                "receipturl",
                "receipt_url",
                "invoiceurl",
                "downloadurl",
                "pdfurl",
                "receiptid",
            }
            if interesting or (("amount" in low or "total" in low) and ("date" in low or "paymentdate" in low)):
                parsed = _json_blob_to_receipt(node, base)
                if parsed:
                    found.append(parsed)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    unique: dict[str, dict[str, Any]] = {}
    for row in found:
        unique[receipt_id(row)] = row
    return list(unique.values())


def filter_recent(rows: list[dict[str, Any]], days: int, now: datetime | None = None) -> list[dict[str, Any]]:
    if days <= 0:
        return list(rows)
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    kept: list[dict[str, Any]] = []
    undated: list[dict[str, Any]] = []
    for row in rows:
        parsed = parse_date(row.get("date"))
        if parsed is None:
            undated.append(row)
        elif parsed >= cutoff:
            kept.append(row)
    return kept + undated


def new_receipts(rows: list[dict[str, Any]], sent_ids: list[str]) -> list[dict[str, Any]]:
    known = set(sent_ids)
    return [row for row in rows if receipt_id(row) not in known]


def format_summary(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No new Spotify receipts."
    lines = [f"Spotify kvitteringer: {len(rows)} stk", ""]
    for row in rows:
        lines.append(
            f"- {row.get('date') or '?'}  {row.get('amount') or ''}  {row.get('title') or receipt_id(row)}"
        )
        if row.get("url"):
            lines.append(f"  {row['url']}")
        if row.get("file"):
            lines.append(f"  file: {row['file']}")
    lines.append("")
    lines.append("These are Spotify receipts, not a custom CVR invoice.")
    return "\n".join(lines)


def build_email(
    to_addr: str,
    rows: list[dict[str, Any]],
    files: list[Path],
    from_addr: str,
) -> EmailMessage:
    message = EmailMessage()
    dates = [row.get("date") for row in rows if row.get("date")]
    extra = f" ({dates[0]}–{dates[-1]})" if dates else ""
    message["Subject"] = f"Spotify kvitteringer: {len(rows)} stk{extra}"
    message["From"] = from_addr
    message["To"] = to_addr
    message.set_content(format_summary(rows))
    for path in files:
        data = path.read_bytes()
        subtype = "pdf" if path.suffix.lower() == ".pdf" else "html"
        maintype = "application" if subtype == "pdf" else "text"
        message.add_attachment(data, maintype=maintype, subtype=subtype, filename=path.name)
    return message


def send_message(message: EmailMessage) -> None:
    import smtplib

    settings = smtp_settings()
    host = settings["host"]
    if not host:
        raise RuntimeError("SPOTIFY_SMTP_HOST (or LOMAX_SMTP_HOST) is not set. See SPOTIFY.md")
    port = int(settings["port"] or "587")
    user = settings["user"]
    password = settings["password"]
    if port == 465:
        smtp: smtplib.SMTP = smtplib.SMTP_SSL(host, port, timeout=30)
    else:
        smtp = smtplib.SMTP(host, port, timeout=30)
        smtp.starttls()
    with smtp:
        if user:
            smtp.login(user, password)
        smtp.send_message(message)


def require_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is missing. On the PC that uses Spotify run:\n"
            "  python3 -m pip install -r requirements-spotify.txt\n"
            "  python3 -m playwright install chromium"
        ) from exc
    return sync_playwright


def session_expired(page) -> bool:
    url = page.url or ""
    if looks_like_login(url):
        return True
    body = ""
    try:
        body = page.inner_text("body")[:2000].lower()
    except Exception:
        return False
    return "log in" in body and "order history" not in body and "ordrehistorik" not in body


def click_by_labels(page, labels: tuple[str, ...], timeout: int = 2500) -> bool:
    for label in labels:
        locator = page.get_by_role("link", name=re.compile(rf"^{re.escape(label)}$", re.I))
        try:
            if locator.count() == 0:
                locator = page.get_by_text(re.compile(label, re.I), exact=False)
            if locator.count() == 0:
                continue
            locator.first.click(timeout=timeout)
            page.wait_for_timeout(800)
            return True
        except Exception:
            continue
    return False


def collect_json_receipts(page, payloads: list[Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for payload in payloads:
        found.extend(extract_receipts_from_json(payload, page.url or DEFAULT_ACCOUNT))
    unique: dict[str, dict[str, Any]] = {}
    for row in found:
        unique[receipt_id(row)] = row
    return list(unique.values())


def open_history(page) -> str:
    for url in HISTORY_URLS:
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(1500)
        if session_expired(page):
            return page.url
        html = page.content()
        if parse_receipts_from_html(html, page.url) or any(
            label.lower() in html.lower() for label in HISTORY_LINK_LABELS + CLICK_LABELS
        ):
            return page.url
    click_by_labels(page, HISTORY_LINK_LABELS)
    page.wait_for_timeout(1500)
    return page.url


def discover_receipts(page, payloads: list[Any]) -> list[dict[str, Any]]:
    html = page.content()
    rows = parse_receipts_from_html(html, page.url)
    for row in collect_json_receipts(page, payloads):
        ident = receipt_id(row)
        if all(receipt_id(existing) != ident for existing in rows):
            rows.append(row)
    if rows:
        return rows
    # Last resort: click through visible payment rows and re-parse.
    texts = [
        "More details",
        "Flere oplysninger",
        "View receipts",
        "Se kvitteringer",
        "Manage",
        "Administrer",
    ]
    for label in texts:
        buttons = page.get_by_text(re.compile(label, re.I))
        try:
            count = min(buttons.count(), 12)
        except Exception:
            count = 0
        for index in range(count):
            try:
                buttons.nth(index).click(timeout=2000)
                page.wait_for_timeout(700)
            except Exception:
                continue
        html = page.content()
        extra = parse_receipts_from_html(html, page.url)
        extra.extend(collect_json_receipts(page, payloads))
        if extra:
            unique: dict[str, dict[str, Any]] = {}
            for row in extra:
                unique[receipt_id(row)] = row
            return list(unique.values())
    return rows


def save_pdf_from_page(page, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    page.pdf(path=str(path), format="A4")
    return path


def download_one(page, row: dict[str, Any], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = out_dir / safe_filename(row, ".pdf")
    html_path = out_dir / safe_filename(row, ".html")
    url = row.get("url")
    if url:
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(800)
    downloaded = None
    try:
        with page.expect_download(timeout=4000) as pending:
            if not click_by_labels(page, ("Download", "Hent", "Download PDF")):
                raise RuntimeError("no download button")
        downloaded = pending.value
    except Exception:
        downloaded = None
    if downloaded is not None:
        target = out_dir / (downloaded.suggested_filename or pdf_path.name)
        downloaded.save_as(str(target))
        return target
    try:
        return save_pdf_from_page(page, pdf_path)
    except Exception:
        html_path.write_text(page.content(), encoding="utf-8")
        return html_path


def run_login(storage: Path, headed: bool = True) -> int:
    sync_playwright = require_playwright()
    storage.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not headed)
        context = browser.new_context(locale="da-DK", accept_downloads=True)
        page = context.new_page()
        page.goto(DEFAULT_ACCOUNT, wait_until="domcontentloaded", timeout=45000)
        print(
            "A browser window opened.\n"
            "1. Log into the Spotify account that pays Premium.\n"
            "2. Wait until you see the account page (not the login form).\n"
            "3. Come back here and press Enter.",
            flush=True,
        )
        try:
            input()
        except EOFError:
            print("No keyboard available to confirm login.", file=sys.stderr)
            browser.close()
            return 2
        if session_expired(page):
            print("Still on a login page. Try --login again after finishing sign-in.", file=sys.stderr)
            browser.close()
            return 2
        context.storage_state(path=str(storage))
        browser.close()
    print(f"Saved login to {storage}. Keep this file private. Then run: python3 spotify_invoices.py")
    return 0


def scrape_receipts(
    storage: Path,
    out_dir: Path,
    headed: bool,
    dump_dir: Path | None,
) -> tuple[list[dict[str, Any]], str]:
    if not storage.exists():
        raise RuntimeError(f"No saved login at {storage}. Run: python3 spotify_invoices.py --login")
    sync_playwright = require_playwright()
    payloads: list[Any] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not headed)
        context = browser.new_context(
            storage_state=str(storage),
            locale="da-DK",
            accept_downloads=True,
        )
        page = context.new_page()

        def on_response(response) -> None:
            try:
                url = response.url.lower()
                ctype = (response.headers or {}).get("content-type", "")
                if "json" not in ctype:
                    return
                if not any(hint in url for hint in JSON_URL_HINTS):
                    return
                payloads.append(response.json())
            except Exception:
                return

        page.on("response", on_response)
        page.goto(DEFAULT_ACCOUNT, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(1200)
        if session_expired(page):
            browser.close()
            raise RuntimeError("Spotify login expired. Run: python3 spotify_invoices.py --login")
        history_url = open_history(page)
        if session_expired(page):
            browser.close()
            raise RuntimeError("Spotify login expired. Run: python3 spotify_invoices.py --login")
        rows = discover_receipts(page, payloads)
        if dump_dir is not None:
            dump_dir.mkdir(parents=True, exist_ok=True)
            (dump_dir / "debug-last.html").write_text(page.content(), encoding="utf-8")
            (dump_dir / "debug-last.json").write_text(
                json.dumps({"url": history_url, "payloads": payloads[:20]}, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
        if not rows:
            html_preview = unescape(re.sub(r"<[^>]+>", " ", page.content()))[:400]
            browser.close()
            raise RuntimeError(
                "Logged in, but no receipts were found on the order-history page. "
                f"Current URL: {history_url}. Page text starts: {html_preview!r}. "
                "Re-run with --headed --dump so we can adjust the clicks."
            )
        for row in rows:
            path = download_one(page, row, out_dir)
            row["file"] = str(path)
        context.storage_state(path=str(storage))
        browser.close()
    return rows, history_url


def send_test_email(to_addr: str) -> int:
    if not to_addr:
        print("No email address. Set SPOTIFY_ALERT_EMAIL in spotify-invoices.env.", file=sys.stderr)
        return 2
    settings = smtp_settings()
    from_addr = settings["from_addr"] or settings["user"] or to_addr
    dummy = Path("/tmp/spotify-receipt-test.txt")
    dummy.write_text("This is a test. Setup works. Not a real Spotify receipt.\n", encoding="utf-8")
    row = {
        "id": "TEST",
        "date": time.strftime("%Y-%m-%d"),
        "amount": "0 kr",
        "title": "Test email. Setup works.",
        "file": str(dummy),
    }
    message = build_email(to_addr, [row], [dummy], from_addr)
    message["Subject"] = "Spotify kvitteringer: test email — setup works"
    print(f"Sending a test email to {to_addr} ...", flush=True)
    send_message(message)
    print("Test email sent. Check that inbox (and spam).")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download Spotify receipts with a saved login and email the new ones.",
    )
    parser.add_argument("--login", action="store_true", help="Open a browser, save the Spotify login, then exit")
    parser.add_argument("--send-test-email", action="store_true", help="Send one test mail and exit")
    parser.add_argument("--dry-run", action="store_true", help="Download/list receipts but do not send mail")
    parser.add_argument("--headed", action="store_true", help="Show the browser window")
    parser.add_argument("--all", action="store_true", help="Do not filter by date (still skips already-sent ids)")
    parser.add_argument("--baseline", action="store_true", help="Remember current receipts without emailing them")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS, help=f"On a new mailbox, only send the last N days (default {DEFAULT_DAYS})")
    parser.add_argument("--email", help="Override SPOTIFY_ALERT_EMAIL")
    parser.add_argument("--env-file", help=f"Env file (default: {DEFAULT_ENV_FILE} or lomax-bonus.env)")
    parser.add_argument("--storage", default=DEFAULT_STORAGE, help="Playwright login file")
    parser.add_argument("--state", default=DEFAULT_STATE, help="Sent-receipt memory file")
    parser.add_argument("--receipt-dir", default=DEFAULT_RECEIPT_DIR, help="Where to save PDFs")
    parser.add_argument("--dump", action="store_true", help="Save the last account HTML/JSON for debugging")
    return parser


def main(argv: list[str] | None = None) -> int:
    pre = argv if argv is not None else sys.argv[1:]
    env_file = None
    if "--env-file" in pre:
        idx = pre.index("--env-file")
        if idx + 1 < len(pre):
            env_file = Path(pre[idx + 1])
    if env_file:
        load_env_file(env_file)
    else:
        for path in default_env_paths():
            if path.exists():
                load_env_file(path)

    args = build_parser().parse_args(argv)
    storage = Path(args.storage)
    to_addr = alert_email(args.email)
    if args.login:
        try:
            return run_login(storage, headed=True)
        except Exception as exc:
            print(str(exc), file=sys.stderr)
            return 2

    if args.send_test_email:
        try:
            return send_test_email(to_addr)
        except Exception as exc:
            print(str(exc), file=sys.stderr)
            return 2

    dump_dir = Path(args.receipt_dir) if args.dump else None
    try:
        rows, history_url = scrape_receipts(
            storage=storage,
            out_dir=Path(args.receipt_dir),
            headed=args.headed,
            dump_dir=dump_dir,
        )
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(f"Found {len(rows)} receipt(s) via {history_url}", flush=True)
    state_path = Path(args.state)
    state = load_state(state_path)
    sent_ids = [str(item) for item in state.get("sent_ids") or []]
    first_run = not sent_ids
    candidates = list(rows)
    if first_run and not args.all and not args.baseline:
        candidates = filter_recent(candidates, args.days)
        print(f"First run: only last {args.days} days (use --all for everything).", flush=True)
    outgoing = [] if args.baseline else new_receipts(candidates, sent_ids)
    print(format_summary(outgoing if not args.baseline else []), flush=True)

    if args.baseline:
        state["sent_ids"] = sorted({*sent_ids, *(receipt_id(row) for row in rows)})
        state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        save_state(state_path, state)
        print(f"Baseline saved to {state_path}. Next run emails only new receipts.")
        return 0

    if args.dry_run:
        print("Dry run; not sending mail and not updating state.")
        return 0

    if not outgoing:
        print("No new receipts to email.")
        state["sent_ids"] = sorted({*sent_ids, *(receipt_id(row) for row in rows)})
        state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        save_state(state_path, state)
        return 0

    if not to_addr:
        print("No email address. Set SPOTIFY_ALERT_EMAIL or pass --email.", file=sys.stderr)
        return 2
    settings = smtp_settings()
    from_addr = settings["from_addr"] or settings["user"] or to_addr
    files = [Path(row["file"]) for row in outgoing if row.get("file") and Path(row["file"]).exists()]
    message = build_email(to_addr, outgoing, files, from_addr)
    send_message(message)
    print(f"Emailed {len(files)} file(s) to {to_addr}")
    state["sent_ids"] = sorted({*sent_ids, *(receipt_id(row) for row in rows)})
    state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    save_state(state_path, state)
    print(f"Saved sent ids to {state_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
