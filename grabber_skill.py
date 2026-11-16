"""Trade Me rental location scraper.

Pick a region and a district, tick "All Suburbs", search, and save the suburb
names and IDs under that region + district in searches.csv. That is all that is
needed to build a results URL.
"""

import csv
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import Locator, Page, sync_playwright

START_URL = "https://www.trademe.co.nz/a/property/residential/rent"
CSV_PATH = Path("searches.csv")
CSV_COLUMNS = ["region", "district", "suburb_name", "suburb_id"]

# Selectors in one place so a site change only needs fixing here
REGION_SELECT = "select[name='region']"
DISTRICT_SELECT = "select[name='district']"
SUBURB_BUTTON = "button.tm-property-location-search-multi-dropdown__suburb-button"
SUBURB_PANEL = "tm-property-dropdown-content"
ALL_SUBURBS_INPUT = "input[name='All Suburbs']"
SEARCH_BUTTON = "button[title='Search']"


# ---------------------------------------------------------------- helpers

def slugify(text: str) -> str:
    """'Hawke's Bay' -> 'hawkes-bay', 'Nelson / Tasman' -> 'nelson-tasman'."""
    text = text.strip().lower().replace("'", "")
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def ask_choice(prompt: str, options: list[str]) -> int:
    """Show a numbered list and keep asking until the user enters a valid
    number. The 1-based number doubles as the <select> index, because index 0
    of each dropdown is the 'All ...' entry that is left out of the list."""
    print(f"\n{prompt}")
    print("-" * 30)
    for i, name in enumerate(options, start=1):
        print(f"{i:>2}. {name}")
    while True:
        answer = input(f"\nEnter a number (1-{len(options)}): ").strip()
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return int(answer)
        print("Invalid choice, try again.")


# ------------------------------------------------------------ page actions

def choose_from_select(page: Page, select_css: str, label: str) -> str:
    """Read a <select>'s options, let the user pick one, select it on the page
    and return its slug (e.g. 'manukau-city')."""
    option_css = f"{select_css} option"
    page.wait_for_selector(option_css, state="attached")  # Angular renders async
    labels = page.locator(option_css).all_text_contents()
    options = [slugify(t) for t in labels if t.strip()][1:]  # drop 'All ...'

    index = ask_choice(f"Select a {label}", options)
    # Call select_option on the <select>, never on its <option> children
    page.locator(select_css).select_option(index=index)
    return options[index - 1]


def open_suburb_panel(page: Page) -> tuple[Locator, list[str]]:
    """Open the suburb dropdown and return (panel, suburb names).

    The property-type dropdown uses the same component, so the panel is the
    one that contains the 'All Suburbs' checkbox."""
    page.locator(SUBURB_BUTTON).click()
    panel = page.locator(SUBURB_PANEL).filter(has=page.locator(ALL_SUBURBS_INPUT))
    # Wait for a real suburb row, not just 'All Suburbs'
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


def search_and_get_suburb_ids(page: Page) -> list[str]:
    """Click Search and return the suburb IDs from the results URL."""
    page.keyboard.press("Escape")  # the dropdown can cover the Search button
    page.locator(SEARCH_BUTTON).click()
    # SPA navigation: wait for the URL to carry the suburb IDs
    page.wait_for_url(lambda url: "suburb=" in url, timeout=15_000)
    return parse_qs(urlparse(page.url).query).get("suburb", [])


# --------------------------------------------------------------------- csv

def load_known_ids(path: Path) -> set[str]:
    """Suburb IDs already saved in the CSV."""
    if not path.exists():
        return set()
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != CSV_COLUMNS:
            raise ValueError(
                f"{path} has different columns ({reader.fieldnames}). "
                "Rename or delete the old file and run again."
            )
        return {row["suburb_id"] for row in reader}


def save_suburbs(
    region: str, district: str, suburbs: dict[str, str], path: Path = CSV_PATH
) -> int:
    """Append suburbs (name -> ID) under this region + district, skipping IDs
    that are already saved. Returns how many were added."""
    known = load_known_ids(path)
    new = {name: sid for name, sid in suburbs.items() if sid not in known}
    if not new:
        return 0

    write_header = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        if write_header:
            writer.writeheader()
        for name, sid in new.items():
            writer.writerow({
                "region": region,
                "district": district,
                "suburb_name": name,
                "suburb_id": sid,
            })
    return len(new)


# -------------------------------------------------------------------- main

def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        try:
            page = browser.new_page()
            page.goto(START_URL)

            region = choose_from_select(page, REGION_SELECT, "region")
            district = choose_from_select(page, DISTRICT_SELECT, "district")

            panel, names = open_suburb_panel(page)
            tick_all_suburbs(panel)
            ids = search_and_get_suburb_ids(page)

            # Names and IDs are paired by position (Nth name <-> Nth ID)
            if len(names) != len(ids):
                raise ValueError(f"{len(names)} suburb names but {len(ids)} IDs")
            suburbs = dict(zip(names, ids))

            added = save_suburbs(region, district, suburbs)
            print(f"\n{region} / {district}: {len(suburbs)} suburbs found, "
                  f"{added} new saved to {CSV_PATH}")
        finally:
            browser.close()  # always close, even if something above fails


if __name__ == "__main__":
    main()