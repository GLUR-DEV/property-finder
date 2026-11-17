"""Open a search URL from build_url.py and collect the listings on every page.

The search API needs credentials that only the site's own code supplies (a
direct call returns 401), and the results page is rendered by the server, so
this reads the listing data the server embeds in each page's HTML instead.
Every field it finds is kept.
"""

import csv
import json
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from playwright.sync_api import Page, sync_playwright

from build_url import get_search_url

MAX_PAGES = 50       # safety cap so a bad stop condition can't loop forever
PAGE_DELAY = 1.5     # seconds to wait between pages

RAW_PATH = Path("response_raw.json")    # first page's embedded JSON, for inspection
HTML_PATH = Path("page.html")           # saved when no listings are found
JSON_PATH = Path("listings.json")       # every listing, all fields, as found
CSV_PATH = Path("listings.csv")         # same listings, flattened to columns

# Angular escapes these characters when it embeds JSON in a page
ESCAPES = {"&a;": "&", "&q;": '"', "&s;": "'", "&l;": "<", "&g;": ">"}


# ------------------------------------------------------------- fetching

def with_page(url: str, number: int) -> str:
    """Return the URL for a given results page (page 1 has no page param)."""
    parts = urlparse(url)
    query = [(k, v) for k, v in parse_qsl(parts.query) if k != "page"]
    if number > 1:
        query.append(("page", str(number)))
    return urlunparse(parts._replace(query=urlencode(query)))


def parse_json(text: str) -> object | None:
    """Parse JSON text, also trying it with Angular's escapes undone."""
    unescaped = text
    for token, char in ESCAPES.items():
        unescaped = unescaped.replace(token, char)
    for candidate in (text, unescaped):
        try:
            return json.loads(candidate)
        except ValueError:
            continue
    return None


def read_embedded_json(page: Page) -> list[tuple[str, object]]:
    """Return (label, data) for every JSON <script> on the page."""
    scripts = page.evaluate(
        """() => [...document.querySelectorAll('script')]
            .filter(s => s.type === 'application/json'
                      || s.type === 'application/ld+json'
                      || /state/i.test(s.id))
            .map(s => ({ id: s.id, type: s.type, text: s.textContent }))"""
    )
    found = []
    for script in scripts:
        data = parse_json(script["text"])
        if data is not None:
            found.append((script["id"] or script["type"], data))
    return found


def find_listings(data: object) -> list[dict]:
    """Pull the listings out of Trade Me's embedded Angular state.

    They live in NGRX_STATE.listing.cachedSearchResults as a dict keyed by
    listing ID; 'ids' holds them in search order. Each entity wraps the real
    data in an 'item' key."""
    if not isinstance(data, dict):
        return []
    cached = data.get("NGRX_STATE", {}).get("listing", {}).get("cachedSearchResults", {})
    entities = cached.get("entities", {})
    ids = cached.get("ids") or list(entities)
    return [entities[str(i)]["item"] for i in ids if "item" in entities.get(str(i), {})]


def listings_on_page(page: Page, number: int) -> list[dict]:
    """Return the listings embedded in the page that is currently loaded."""
    embedded = read_embedded_json(page)

    best_label, best_data, best = "", None, []
    for label, data in embedded:
        listings = find_listings(data)
        if len(listings) > len(best):
            best_label, best_data, best = label, data, listings

    if number == 1:
        print(f"JSON scripts on the page: "
              f"{[(label, len(find_listings(d))) for label, d in embedded]}")
        if best:
            print(f"Listings taken from: '{best_label}'\n")
            save_json(RAW_PATH, best_data)
        else:
            HTML_PATH.write_text(page.content(), encoding="utf-8")
            print(f"No listing data found. Page HTML saved to {HTML_PATH}")
    return best


def collect_all_pages(page: Page, url: str) -> list[dict]:
    """Walk through the results pages, collecting listings until a page adds
    nothing new (empty, repeated, or past the last page)."""
    collected: list[dict] = []
    seen: set[str] = set()

    for number in range(1, MAX_PAGES + 1):
        page.goto(with_page(url, number), wait_until="domcontentloaded")
        listings = listings_on_page(page, number)

        fresh = []
        for listing in listings:
            key = json.dumps(listing, sort_keys=True)  # whole listing as identity
            if key not in seen:
                seen.add(key)
                fresh.append(listing)

        print(f"Page {number}: {len(listings)} listings, {len(fresh)} new")
        if not fresh:
            break
        collected.extend(fresh)
        time.sleep(PAGE_DELAY)
    else:
        print(f"Stopped at the {MAX_PAGES}-page limit")

    return collected


# --------------------------------------------------------------- saving

def save_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def flatten(obj: dict, prefix: str = "") -> dict:
    """Flatten nested objects into dotted keys ({'a': {'b': 1}} -> {'a.b': 1}).
    Lists are kept as JSON text in a single cell."""
    row: dict = {}
    for key, value in obj.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict):
            row.update(flatten(value, f"{name}."))
        elif isinstance(value, list):
            row[name] = json.dumps(value, ensure_ascii=False)
        else:
            row[name] = value
    return row


def save_csv(path: Path, listings: list[dict]) -> None:
    """One row per listing, one column per field (union across all listings)."""
    rows = [flatten(listing) for listing in listings]
    columns = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


# ----------------------------------------------------------------- main

def main() -> None:
    url = get_search_url()
    print(f"\nOpening {url}\n")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        try:
            listings = collect_all_pages(browser.new_page(), url)
        finally:
            browser.close()

    if not listings:
        raise SystemExit("No listings found.")

    save_json(JSON_PATH, listings)
    save_csv(CSV_PATH, listings)
    print(f"\n{len(listings)} listings saved to {JSON_PATH} and {CSV_PATH}")
    print("Fields on the first listing:", ", ".join(listings[0].keys()))


if __name__ == "__main__":
    main()