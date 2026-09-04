#!/usr/bin/env python3
"""Check Lomax listing pages for the best KvartalsBonus stickers.

stdlib-only. Re-run whenever you want a fresh ranking:

    python3 lomax_bonus.py
    python3 lomax_bonus.py --min-bonus 50
    python3 lomax_bonus.py --csv bonuses.csv --json bonuses.json

The first page is used to discover how many pages exist. Every later run
compares against lomax-bonus-latest.json (unless you pass --no-compare)
so you can see what appeared, disappeared, or changed bonus.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import html as htmlmod
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

DEFAULT_LISTING = "https://www.lomax.dk/soeg/"
DEFAULT_HITS = 48
DEFAULT_MIN_BONUS = 25
DEFAULT_WORKERS = 8
DEFAULT_STATE = "lomax-bonus-latest.json"
USER_AGENT = (
    "Mozilla/5.0 (compatible; LomaxBonusCheck/1.0; +https://github.com/ValbyLyntryk/apps) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

NAME_RE = re.compile(r'data-cnstrc-item-name="([^"]*)"')
ID_RE = re.compile(r'data-cnstrc-item-id="([^"]*)"')
PRICE_RE = re.compile(r'data-cnstrc-item-price="([^"]*)"')
LINK_RE = re.compile(
    r'<a href="([^"]+)"[^>]*>\s*<h5 class="product-name">',
    re.IGNORECASE,
)
H5_RE = re.compile(r'<h5 class="product-name">(.*?)</h5>', re.DOTALL)
DESC_RE = re.compile(r'<p class="product-description[^"]*">(.*?)</p>', re.DOTALL)
VAR_RE = re.compile(r"Varenr\s+(\d+)")
BADGE_RE = re.compile(r'<span class="badge[^"]*">([^<]+)</span>')
BONUS_RE = re.compile(
    r'<div class="badge badge-bonus[^"]*">\s*'
    r'<span class="[^"]*">\s*(\d+)\s*%\s*</span>\s*'
    r'<span class="[^"]*">\s*Bonus',
    re.IGNORECASE,
)
WAS_PRICE_RE = re.compile(
    r"<del>\s*F(?:&oslash;|ø|&#xF8;)r:\s*([0-9.]+,[0-9]{2})\s*kr",
    re.IGNORECASE,
)
TOTAL_PAGES_RE = re.compile(r'data-total-pages="(\d+)"')
PRODUCT_COUNT_RE = re.compile(
    r"Filtre\s+(\d[\d.]*)\s+Produkter|>(\d[\d.]*)\s+Produkter<",
    re.IGNORECASE,
)


def unescape(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = htmlmod.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def parse_dk_price(value: str | None) -> float | None:
    if not value:
        return None
    cleaned = value.strip().replace(".", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_listing_price(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return parse_dk_price(value)


def format_dkk(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") + " kr."


def absolute_url(href: str | None, base: str) -> str | None:
    if not href:
        return None
    return urllib.parse.urljoin(base, href)


def listing_url(base: str, hits: int, page: int) -> str:
    parts = urllib.parse.urlsplit(base)
    query = dict(urllib.parse.parse_qsl(parts.query, keep_blank_values=True))
    query["hits"] = str(hits)
    query["page"] = str(page)
    return urllib.parse.urlunsplit(
        (
            parts.scheme or "https",
            parts.netloc or "www.lomax.dk",
            parts.path or "/soeg/",
            urllib.parse.urlencode(query),
            "",
        )
    )


def fetch_url(url: str, retries: int = 4, timeout: int = 45) -> str:
    last_err: Exception | None = None
    for attempt in range(retries):
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "da-DK,da;q=0.9,en;q=0.8",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"failed to fetch {url}: {last_err}")


def detect_total_pages(page_html: str, fallback: int = 1) -> int:
    match = TOTAL_PAGES_RE.search(page_html)
    if match:
        return max(1, int(match.group(1)))
    return fallback


def product_chunks(page_html: str) -> list[str]:
    marker = 'class="product-list-item'
    parts = page_html.split(marker)
    if len(parts) < 2:
        return []
    chunks = []
    for part in parts[1:]:
        cut_at = len(part)
        for token in (
            'class="pagination"',
            "data-total-pages",
            'class="product-list-container"',
            "<footer",
        ):
            idx = part.find(token)
            if idx != -1:
                cut_at = min(cut_at, idx)
        chunks.append(part[:cut_at])
    return chunks


def parse_product(chunk: str, page: int, base: str) -> dict[str, Any] | None:
    item_id = (ID_RE.search(chunk) or [None, None])[1]
    name_raw = (NAME_RE.search(chunk) or [None, None])[1]
    price_raw = (PRICE_RE.search(chunk) or [None, None])[1]
    href = (LINK_RE.search(chunk) or [None, None])[1]
    heading = H5_RE.search(chunk)
    desc = DESC_RE.search(chunk)
    varenr = (VAR_RE.search(chunk) or [None, None])[1]
    badges = [unescape(badge) for badge in BADGE_RE.findall(chunk)]
    bonus_match = BONUS_RE.search(chunk)
    was_match = WAS_PRICE_RE.search(chunk)

    name = htmlmod.unescape(name_raw) if name_raw else (
        unescape(heading.group(1)) if heading else None
    )
    if not name and not item_id:
        return None

    price = parse_listing_price(price_raw)
    was_price = parse_dk_price(was_match.group(1)) if was_match else None
    discount_pct = None
    if price is not None and was_price and was_price > 0:
        discount_pct = round((1 - price / was_price) * 100)

    return {
        "page": page,
        "varenr": varenr or item_id,
        "name": name,
        "description": unescape(desc.group(1)) if desc else "",
        "bonus_pct": int(bonus_match.group(1)) if bonus_match else None,
        "price": price,
        "was_price": was_price,
        "discount_pct": discount_pct,
        "badges": badges,
        "url": absolute_url(href, base),
    }


def scrape_page(url: str, page: int, base: str) -> tuple[int, list[dict[str, Any]], str | None]:
    try:
        html = fetch_url(url)
    except Exception as exc:  # noqa: BLE001
        return page, [], str(exc)
    products = []
    for chunk in product_chunks(html):
        parsed = parse_product(chunk, page, base)
        if parsed:
            products.append(parsed)
    return page, products, None


def dedupe(products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for item in sorted(products, key=lambda row: (row["page"], row.get("varenr") or "")):
        key = str(item.get("varenr") or item.get("name") or id(item))
        if key not in by_id:
            by_id[key] = item
    return list(by_id.values())


def rank(products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        products,
        key=lambda row: (
            -(row.get("bonus_pct") or 0),
            -(row.get("discount_pct") or 0),
            row.get("price") if row.get("price") is not None else 10**12,
            row.get("name") or "",
        ),
    )


def filter_min_bonus(products: list[dict[str, Any]], minimum: int) -> list[dict[str, Any]]:
    return [row for row in products if (row.get("bonus_pct") or 0) >= minimum]


def bonus_counts(products: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(row.get("bonus_pct") for row in products if row.get("bonus_pct") is not None)
    return {f"{pct}%": counts[pct] for pct in sorted(counts, reverse=True)}


def load_state(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def save_state(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def compare_runs(
    previous: list[dict[str, Any]],
    current: list[dict[str, Any]],
    minimum: int,
) -> dict[str, list[dict[str, Any]]]:
    prev = {
        str(row.get("varenr")): row
        for row in previous
        if (row.get("bonus_pct") or 0) >= minimum
    }
    curr = {
        str(row.get("varenr")): row
        for row in current
        if (row.get("bonus_pct") or 0) >= minimum
    }
    appeared = [curr[key] for key in curr.keys() - prev.keys()]
    disappeared = [prev[key] for key in prev.keys() - curr.keys()]
    changed = []
    for key in curr.keys() & prev.keys():
        old_bonus = prev[key].get("bonus_pct")
        new_bonus = curr[key].get("bonus_pct")
        if old_bonus != new_bonus:
            changed.append(
                {
                    **curr[key],
                    "previous_bonus_pct": old_bonus,
                }
            )
    return {
        "appeared": rank(appeared),
        "disappeared": rank(disappeared),
        "changed": rank(changed),
    }


def print_table(products: list[dict[str, Any]], limit: int | None = None) -> None:
    rows = products if limit is None else products[:limit]
    if not rows:
        print("No products matched.")
        return
    headers = ("Bonus", "Price", "Was", "Sale", "Badges", "Varenr", "Product")
    table = []
    for row in rows:
        table.append(
            (
                f"{row.get('bonus_pct')}%" if row.get("bonus_pct") is not None else "",
                format_dkk(row.get("price")),
                format_dkk(row.get("was_price")),
                f"-{row['discount_pct']}%" if row.get("discount_pct") is not None else "",
                ", ".join(row.get("badges") or []),
                str(row.get("varenr") or ""),
                (row.get("name") or "")[:70],
            )
        )
    widths = [len(header) for header in headers]
    for line in table:
        for i, cell in enumerate(line):
            widths[i] = max(widths[i], len(cell))
    fmt = "  ".join(f"{{:<{width}}}" for width in widths)
    print(fmt.format(*headers))
    print("  ".join("-" * width for width in widths))
    for line in table:
        print(fmt.format(*line))


def print_changes(diff: dict[str, list[dict[str, Any]]]) -> None:
    appeared = diff["appeared"]
    disappeared = diff["disappeared"]
    changed = diff["changed"]
    if not appeared and not disappeared and not changed:
        print("\nNo bonus changes since the last saved run.")
        return
    print("\nChanges since last saved run")
    print("----------------------------")
    for row in appeared:
        print(
            f"  NEW   {row.get('bonus_pct')}%  {row.get('varenr')}  {row.get('name')}"
        )
    for row in changed:
        print(
            f"  CHG   {row.get('previous_bonus_pct')}% -> {row.get('bonus_pct')}%  "
            f"{row.get('varenr')}  {row.get('name')}"
        )
    for row in disappeared:
        print(
            f"  GONE  {row.get('bonus_pct')}%  {row.get('varenr')}  {row.get('name')}"
        )


def write_csv(path: Path, products: list[dict[str, Any]]) -> None:
    fields = [
        "bonus_pct",
        "price",
        "was_price",
        "discount_pct",
        "varenr",
        "name",
        "badges",
        "page",
        "url",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in products:
            writer.writerow(
                {
                    "bonus_pct": row.get("bonus_pct"),
                    "price": row.get("price"),
                    "was_price": row.get("was_price"),
                    "discount_pct": row.get("discount_pct"),
                    "varenr": row.get("varenr"),
                    "name": row.get("name"),
                    "badges": "|".join(row.get("badges") or []),
                    "page": row.get("page"),
                    "url": row.get("url"),
                }
            )


def scrape_listing(
    base: str,
    hits: int,
    workers: int,
    max_pages: int | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    first_url = listing_url(base, hits, 1)
    first_html = fetch_url(first_url)
    discovered = detect_total_pages(first_html)
    total_pages = discovered if max_pages is None else min(discovered, max_pages)

    first_products = []
    for chunk in product_chunks(first_html):
        parsed = parse_product(chunk, 1, base)
        if parsed:
            first_products.append(parsed)

    products = list(first_products)
    errors: list[dict[str, str]] = []
    pages_ok = 1 if first_products or "product-list-item" in first_html else 0

    remaining = list(range(2, total_pages + 1))
    done = 1
    if remaining:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(scrape_page, listing_url(base, hits, page), page, base): page
                for page in remaining
            }
            for future in concurrent.futures.as_completed(futures):
                page, page_products, err = future.result()
                done += 1
                if err:
                    errors.append({"page": str(page), "error": err})
                else:
                    pages_ok += 1
                    products.extend(page_products)
                if done == total_pages or done % 20 == 0:
                    print(
                        f"progress {done}/{total_pages} products={len(products)} errors={len(errors)}",
                        file=sys.stderr,
                    )

    unique = dedupe(products)
    meta = {
        "listing": listing_url(base, hits, 1),
        "pages_discovered": discovered,
        "pages_scraped": total_pages,
        "pages_ok": pages_ok,
        "errors": errors,
        "product_cards_seen": len(products),
        "unique_products": len(unique),
        "bonus_distribution": bonus_counts(unique),
    }
    return unique, meta


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Scrape Lomax for the best bonus stickers and rank them.",
    )
    parser.add_argument(
        "--url",
        default=DEFAULT_LISTING,
        help=f"Listing URL to scrape (default: {DEFAULT_LISTING})",
    )
    parser.add_argument(
        "--hits",
        type=int,
        default=DEFAULT_HITS,
        help=f"Products per page (default: {DEFAULT_HITS})",
    )
    parser.add_argument(
        "--pages",
        type=int,
        default=None,
        help="Scrape at most this many pages (default: all discovered pages)",
    )
    parser.add_argument(
        "--min-bonus",
        type=int,
        default=DEFAULT_MIN_BONUS,
        help=(
            "Only show products at or above this bonus percent "
            f"(default: {DEFAULT_MIN_BONUS}; 5%% is the everyday baseline)"
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Show only the top N matches in the table",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=f"Parallel page fetches (default: {DEFAULT_WORKERS})",
    )
    parser.add_argument(
        "--state",
        default=DEFAULT_STATE,
        help=f"JSON file used to remember the last run (default: {DEFAULT_STATE})",
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="Do not write the state file",
    )
    parser.add_argument(
        "--no-compare",
        action="store_true",
        help="Do not compare against the previous state file",
    )
    parser.add_argument("--json", dest="json_path", help="Write the ranked matches as JSON")
    parser.add_argument("--csv", dest="csv_path", help="Write the ranked matches as CSV")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    started = time.time()
    products, meta = scrape_listing(
        base=args.url,
        hits=args.hits,
        workers=args.workers,
        max_pages=args.pages,
    )
    matches = rank(filter_min_bonus(products, args.min_bonus))
    elapsed = round(time.time() - started, 1)

    payload = {
        "scraped_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "elapsed_sec": elapsed,
        "min_bonus": args.min_bonus,
        **meta,
        "matches": matches,
        "all_products": products,
    }

    print(
        f"Scraped {meta['unique_products']} products on {meta['pages_ok']}/{meta['pages_scraped']} "
        f"pages in {elapsed}s"
    )
    print("Bonus distribution: " + ", ".join(
        f"{label}={count}" for label, count in meta["bonus_distribution"].items()
    ))
    print(f"\nBest bonuses (>={args.min_bonus}%)")
    print_table(matches, args.limit)

    state_path = Path(args.state)
    previous = None if args.no_compare else load_state(state_path)
    if previous and previous.get("all_products"):
        print_changes(compare_runs(previous["all_products"], products, args.min_bonus))
    elif previous and previous.get("matches"):
        print_changes(compare_runs(previous["matches"], matches, args.min_bonus))

    if not args.no_save:
        save_state(state_path, payload)
        print(f"\nSaved run to {state_path}", file=sys.stderr)

    if args.json_path:
        Path(args.json_path).write_text(
            json.dumps({k: payload[k] for k in payload if k != "all_products"}, ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
    if args.csv_path:
        write_csv(Path(args.csv_path), matches)

    if meta["errors"]:
        print(f"\n{len(meta['errors'])} page(s) failed. Re-run to fill gaps.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
