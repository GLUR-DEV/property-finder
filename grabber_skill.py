"""Trade Me rental search helper.

Flow: pick a region -> pick a district -> tick "All Suburbs" -> click Search,
then read the region / district / suburb / category IDs from the result URL.
"""

import csv
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import Locator, Page, sync_playwright

START_URL = "https://www.trademe.co.nz/a/property/residential/rent"
CSV_PATH = Path("searches.csv")
CSV_COLUMNS = [
    "search_id", "searched_at", "region_name", "region_id",
    "district_name", "district_id", "category_id",
    "suburb_name", "suburb_id",
]

# Selectors in one place so a site change only needs fixing here
REGION_SELECT = "select[name='region']"
DISTRICT_SELECT = "select[name='district']"
SUBURB_BUTTON = "button.tm-property-location-search-multi-dropdown__suburb-button"
SUBURB_PANEL = "tm-property-dropdown-content"
ALL_SUBURBS_INPUT = "input[name='All Suburbs']"
SEARCH_BUTTON = "button[title='Search']"


@dataclass
class SearchParams:
    """IDs the site put in the results URL."""
    region: str | None
    district: str | None
    category: str | None
    suburb_ids: list[str] = field(default_factory=list)


@dataclass
class SearchResult:
    """Names the user picked, paired with the matching IDs from the URL."""
    region_name: str
    region_id: str | None
    district_name: str
    district_id: str | None
    category_id: str | None
    suburbs: dict[str, str]  # suburb name -> suburb ID


# ---------------------------------------------------------------- helpers

def slugify(text: str) -> str:
    """'Hawke's Bay' -> 'hawkes-bay', 'Nelson / Tasman' -> 'nelson-tasman'."""
    text = text.strip().lower().replace("'", "")
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def show_options(options: list[str]) -> None:
    """Print a numbered list (1-based) for the user to choose from."""
    print("\nAvailable options")
    print("-" * (max(len(o) for o in options) + 8))
    for i, name in enumerate(options, start=1):
        print(f"{i:>2}. {name}")
    print()


def ask_choice(prompt: str, options: list[str]) -> int:
    """Show the options and keep asking until the user enters a valid number.

    Returns the 1-based number, which doubles as the <select> index because
    index 0 of each dropdown is the 'All ...' entry that we skip in the list.
    """
    show_options(options)
    while True:
        answer = input(f"{prompt} (1-{len(options)}): ").strip()
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return int(answer)
        print("Invalid choice, try again.")


# ------------------------------------------------------------ page actions

def read_select_options(page: Page, select_css: str) -> list[str]:
    """Return the slugified option labels of a <select>, minus the first
    'All ...' entry. Waits for Angular to render the options first."""
    option_css = f"{select_css} option"
    page.wait_for_selector(option_css, state="attached")
    labels = page.locator(option_css).all_text_contents()
    return [slugify(t) for t in labels if t.strip()][1:]


def choose_from_select(page: Page, select_css: str, label: str) -> str:
    """Ask the user to pick an option, select it on the page, and return the
    name of the chosen option (slugified, e.g. 'manukau-city')."""
    options = read_select_options(page, select_css)
    index = ask_choice(f"Select a {label} by number", options)
    # Call select_option on the <select>, never on its <option> children
    page.locator(select_css).select_option(index=index)
    return options[index - 1]


def get_suburb_panel(page: Page) -> Locator:
    """The suburb dropdown panel (the property-type dropdown uses the same
    component, so filter on the one that holds the 'All Suburbs' checkbox)."""
    return page.locator(SUBURB_PANEL).filter(has=page.locator(ALL_SUBURBS_INPUT))


def open_suburb_panel_and_read_names(page: Page) -> tuple[Locator, list[str]]:
    """Open the suburb dropdown and return (panel, suburb names)."""
    page.locator(SUBURB_BUTTON).click()
    panel = get_suburb_panel(page)
    # Wait until at least one real suburb row exists, not just 'All Suburbs'
    panel.locator("input[type='checkbox']").nth(1).wait_for(state="attached")

    names = [
        t.strip()
        for t in panel.locator(".o-checkbox__label-text").all_text_contents()
        if t.strip() and t.strip() != "All Suburbs"
    ]
    return panel, names


def tick_all_suburbs(panel: Locator) -> None:
    """Tick 'All Suburbs' if it isn't already ticked."""
    checkbox = panel.locator(ALL_SUBURBS_INPUT)
    if not checkbox.is_checked():
        # The input is visually hidden, so click its label like a user would
        panel.locator("label", has_text="All Suburbs").click()
    if not checkbox.is_checked():
        raise RuntimeError("Could not tick 'All Suburbs'")


def run_search(page: Page) -> str:
    """Close the dropdown, click Search and return the resulting URL."""
    page.keyboard.press("Escape")  # dropdown can cover the Search button
    page.locator(SEARCH_BUTTON).click()
    # SPA navigation: wait for the URL to carry the search params
    page.wait_for_url(lambda url: "region=" in url, timeout=15_000)
    return page.url


def parse_search_url(url: str) -> SearchParams:
    """Pull region, district, category and suburb IDs out of a results URL."""
    params = parse_qs(urlparse(url).query)  # repeated keys become lists
    first = lambda key: params.get(key, [None])[0]
    return SearchParams(
        region=first("region"),
        district=first("district"),
        category=first("category"),
        suburb_ids=params.get("suburb", []),
    )


def build_result(
    region_name: str,
    district_name: str,
    suburb_names: list[str],
    search: SearchParams,
) -> SearchResult:
    """Pair each chosen name with its ID.

    Suburbs are paired by position: the Nth name in the dropdown is assumed
    to match the Nth suburb ID in the URL.
    """
    if len(suburb_names) != len(search.suburb_ids):
        raise ValueError(
            f"{len(suburb_names)} suburb names but {len(search.suburb_ids)} IDs, "
            "so they can't be paired by position"
        )
    return SearchResult(
        region_name=region_name,
        region_id=search.region,
        district_name=district_name,
        district_id=search.district,
        category_id=search.category,
        suburbs=dict(zip(suburb_names, search.suburb_ids)),
    )


# --------------------------------------------------------------------- csv

def load_existing(path: Path) -> tuple[int, set[str]]:
    """Return (highest search_id so far, suburb IDs already saved)."""
    if not path.exists():
        return 0, set()
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    last_id = max((int(r["search_id"]) for r in rows), default=0)
    return last_id, {r["suburb_id"] for r in rows}


def save_search(result: SearchResult, path: Path = CSV_PATH) -> int:
    """Append this search to the CSV, one row per suburb, skipping suburbs
    that are already saved. Returns how many new suburbs were added.

    The check uses the suburb ID rather than the name, because the same name
    can exist in different districts (they have different IDs), while a
    suburb that is already saved always has the same ID.
    """
    last_id, known_ids = load_existing(path)
    new_suburbs = {
        name: sid for name, sid in result.suburbs.items() if sid not in known_ids
    }
    if not new_suburbs:
        return 0  # nothing new, so don't use up a search_id

    search_id = last_id + 1
    searched_at = datetime.now().astimezone().isoformat(timespec="seconds")
    write_header = not path.exists()

    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        if write_header:
            writer.writeheader()
        for name, sid in new_suburbs.items():
            writer.writerow({
                "search_id": search_id,
                "searched_at": searched_at,
                "region_name": result.region_name,
                "region_id": result.region_id,
                "district_name": result.district_name,
                "district_id": result.district_id,
                "category_id": result.category_id,
                "suburb_name": name,
                "suburb_id": sid,
            })
    return len(new_suburbs)


# -------------------------------------------------------------------- main

def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        try:
            page = browser.new_page()
            page.goto(START_URL)

            region_name = choose_from_select(page, REGION_SELECT, "region")
            district_name = choose_from_select(page, DISTRICT_SELECT, "district")

            panel, suburb_names = open_suburb_panel_and_read_names(page)
            print(f"{len(suburb_names)} suburbs: {suburb_names}")

            tick_all_suburbs(panel)
            result_url = run_search(page)
            print(result_url)

            search = parse_search_url(result_url)
            result = build_result(region_name, district_name, suburb_names, search)

            print(f"region:   {result.region_name} -> {result.region_id}")
            print(f"district: {result.district_name} -> {result.district_id}")
            print(f"category: {result.category_id}")
            print(f"suburbs ({len(result.suburbs)}):")
            for name, suburb_id in result.suburbs.items():
                print(f"  {name} -> {suburb_id}")

            added = save_search(result)
            skipped = len(result.suburbs) - added
            print(f"\nSaved {added} new suburbs to {CSV_PATH} ({skipped} already there)")
        finally:
            browser.close()  # always close, even if something above fails


if __name__ == "__main__":
    main()