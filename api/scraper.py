"""Playwright work: collect listings from results pages, and read locations from
the site. Adapted from get_listings.py and grabber_skill.py with no prompts.

Every function here opens its own browser and must be called from a worker
thread (Playwright's sync API cannot run inside an asyncio event loop)."""

import json
import re
import time
from typing import Callable
from urllib.parse import parse_qs, parse_qsl, urlencode, urlparse, urlunparse

from playwright.sync_api import Locator, Page, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeout

import config

# progress(page_number, listings_so_far, message)
Progress = Callable[[int, int, str], None]

# Angular escapes these characters when it embeds JSON in a page
ESCAPES = {"&a;": "&", "&q;": '"', "&s;": "'", "&l;": "<", "&g;": ">"}
DATE_PREFIX = "__date__:"


# --------------------------------------------------------- extraction

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


def find_listings(data: object) -> list[dict]:
    """Listings live in NGRX_STATE.listing.cachedSearchResults: a dict keyed by
    listing ID, with 'ids' giving search order and each entity wrapping the
    listing in 'item'."""
    if not isinstance(data, dict):
        return []
    state = data.get("NGRX_STATE")
    if not isinstance(state, dict):
        return []
    cached = state.get("listing", {}).get("cachedSearchResults", {})
    entities = cached.get("entities", {})
    ids = cached.get("ids") or list(entities)
    return [entities[str(i)]["item"] for i in ids if "item" in entities.get(str(i), {})]


def clean_dates(node: object) -> object:
    """Strip the '__date__:' prefix the site puts on timestamps."""
    if isinstance(node, dict):
        return {k: clean_dates(v) for k, v in node.items()}
    if isinstance(node, list):
        return [clean_dates(v) for v in node]
    if isinstance(node, str) and node.startswith(DATE_PREFIX):
        return node[len(DATE_PREFIX):]
    return node


def read_embedded_json(page: Page) -> list[tuple[str, object]]:
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


def with_page(url: str, number: int) -> str:
    """URL for a given results page (page 1 has no page param)."""
    parts = urlparse(url)
    query = [(k, v) for k, v in parse_qsl(parts.query) if k != "page"]
    if number > 1:
        query.append(("page", str(number)))
    return urlunparse(parts._replace(query=urlencode(query)))


# ----------------------------------------------------------- listings

def fetch_listings(url: str, progress: Progress, headless: bool | None = None) -> list[dict]:
    """Walk the results pages for a search URL until a page adds nothing new.
    headless overrides the HEADLESS setting for this one scrape."""
    collected: list[dict] = []
    seen: set[str] = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=config.HEADLESS if headless is None else headless)
        try:
            page = browser.new_page()
            for number in range(1, config.MAX_PAGES + 1):
                progress(number, len(collected), f"Reading page {number}")
                page.goto(with_page(url, number), wait_until="domcontentloaded")

                on_page: list[dict] = []
                for _, data in read_embedded_json(page):
                    on_page = find_listings(data)
                    if on_page:
                        if number == 1:
                            config.RAW_JSON.write_text(
                                json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
                        break
                if number == 1 and not on_page:
                    config.DEBUG_HTML.write_text(page.content(), encoding="utf-8")

                fresh = []
                for listing in on_page:
                    key = str(listing.get("listingId") or json.dumps(listing, sort_keys=True))
                    if key not in seen:
                        seen.add(key)
                        fresh.append(listing)
                if not fresh:
                    break
                collected.extend(fresh)
                progress(number, len(collected), f"Page {number}: {len(fresh)} new listings")
                time.sleep(config.PAGE_DELAY)
        finally:
            browser.close()

    return clean_dates(collected)  # type: ignore[return-value]


# ---------------------------------------------------------- locations

# Selectors in one place so a site change only needs fixing here
REGION_SELECT = "select[name='region']"
DISTRICT_SELECT = "select[name='district']"
SUBURB_BUTTON = "button.tm-property-location-search-multi-dropdown__suburb-button"
SUBURB_PANEL = "tm-property-dropdown-content"
ALL_SUBURBS_INPUT = "input[name='All Suburbs']"
SEARCH_BUTTON = "button[title='Search']"


def slugify(text: str) -> str:
    text = text.strip().lower().replace("'", "")
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def select_options(page: Page, select_css: str, timeout_ms: int = 15_000) -> list[str]:
    """Slugs of a <select>'s options, without the leading 'All ...' entry.

    Waits for more than one <option>: the select already holds its 'All ...'
    placeholder before Angular fills in the real entries (the district select
    does this until a region is chosen), so waiting for a single option returns
    too early with an empty list."""
    option_css = f"{select_css} option"
    try:
        page.wait_for_function(
            "css => document.querySelectorAll(css).length > 1",
            arg=option_css,
            timeout=timeout_ms,
        )
    except PlaywrightTimeout:
        raise ValueError(f"Trade Me didn't load any options for {select_css}")
    labels = page.locator(option_css).all_text_contents()
    return [slugify(t) for t in labels if t.strip()][1:]


def choose(page: Page, select_css: str, slug: str, what: str) -> None:
    options = select_options(page, select_css)
    if slug not in options:
        raise ValueError(f"Unknown {what} '{slug}'")
    # Angular adds its ng-* form classes once it has bound the <select>. A choice
    # made before that fires its change event with nobody listening, so the
    # dependent district list never fills.
    page.wait_for_selector(f"{select_css}.ng-untouched", state="attached", timeout=15_000)
    # Index 0 is the 'All ...' entry, so the option's index is its position + 1
    page.locator(select_css).select_option(index=options.index(slug) + 1)


def choose_region(page: Page, region: str, attempts: int = 3) -> list[str]:
    """Pick a region and return its districts, re-picking if they don't load.

    The district select starts disabled with only its 'All districts' entry and
    is filled in by Trade Me's page after the region changes."""
    for attempt in range(1, attempts + 1):
        choose(page, REGION_SELECT, region, "region")
        try:
            return select_options(page, DISTRICT_SELECT, timeout_ms=8_000)
        except ValueError:
            if attempt == attempts:
                raise
            # Bounce back to 'All New Zealand' so picking the region is a real change again
            page.locator(REGION_SELECT).select_option(index=0)
            page.wait_for_timeout(500)
    return []


def site_regions() -> list[str]:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=config.HEADLESS)
        try:
            page = browser.new_page()
            page.goto(config.START_URL)
            return select_options(page, REGION_SELECT)
        finally:
            browser.close()


def site_districts(region: str) -> list[str]:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=config.HEADLESS)
        try:
            page = browser.new_page()
            page.goto(config.START_URL)
            districts = choose_region(page, region)
            if not districts:
                raise ValueError(f"No districts found for region '{region}'")
            return districts
        finally:
            browser.close()


def open_suburb_panel(page: Page) -> tuple[Locator, list[str]]:
    page.locator(SUBURB_BUTTON).click()
    panel = page.locator(SUBURB_PANEL).filter(has=page.locator(ALL_SUBURBS_INPUT))
    panel.locator("input[type='checkbox']").nth(1).wait_for(state="attached")
    names = [
        t.strip()
        for t in panel.locator(".o-checkbox__label-text").all_text_contents()
        if t.strip() and t.strip() != "All Suburbs"
    ]
    return panel, names


def grab_suburbs(region: str, district: str) -> list[tuple[str, str]]:
    """Return (suburb name, suburb id) for every suburb in a district."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=config.HEADLESS)
        try:
            page = browser.new_page()
            page.goto(config.START_URL)
            choose_region(page, region)
            choose(page, DISTRICT_SELECT, district, "district")

            panel, names = open_suburb_panel(page)
            checkbox = panel.locator(ALL_SUBURBS_INPUT)
            if not checkbox.is_checked():
                # The input is visually hidden, so click its label like a user would
                panel.locator("label", has_text="All Suburbs").click()
            if not checkbox.is_checked():
                raise RuntimeError("Could not tick 'All Suburbs'")

            page.keyboard.press("Escape")  # the dropdown can cover the Search button
            page.locator(SEARCH_BUTTON).click()
            page.wait_for_url(lambda u: "suburb=" in u, timeout=15_000)
            ids = parse_qs(urlparse(page.url).query).get("suburb", [])
        finally:
            browser.close()

    # Names and IDs are paired by position (Nth name <-> Nth ID)
    if len(names) != len(ids):
        raise ValueError(f"{len(names)} suburb names but {len(ids)} IDs")
    return list(zip(names, ids))