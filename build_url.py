"""Build a Trade Me rental search URL from the locations saved in searches.csv.

searches.csv has one row per suburb (region, district, suburb_name, suburb_id).
You pick a region, then a district, then one or more suburbs, all from what is
on file, and the script prints the matching search URL.
"""

import csv
import re
from pathlib import Path
from urllib.parse import urlencode

CSV_PATH = Path("searches.csv")

# Results URLs look like:
#   ROOT_URL/<region-slug>/<district-slug>/search?suburb=...&suburb=...
ROOT_URL = "https://www.trademe.co.nz/a/property/residential/rent"

# Choices for the property_type filter
PROPERTY_TYPES = ["apartment", "car park", "house", "townhouse", "unit"]

# region -> district -> [(suburb name, suburb ID), ...]
Locations = dict[str, dict[str, list[tuple[str, str]]]]


# ---------------------------------------------------------------- data

def load_locations(path: Path = CSV_PATH) -> Locations:
    """Read the CSV into a nested region -> district -> suburbs lookup."""
    if not path.exists():
        raise SystemExit(f"{path} not found. Run property_finder.py first.")

    locations: Locations = {}
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            suburbs = locations.setdefault(row["region"], {}).setdefault(row["district"], [])
            entry = (row["suburb_name"], row["suburb_id"])
            if entry not in suburbs:
                suburbs.append(entry)
    if not locations:
        raise SystemExit(f"{path} has no saved locations yet.")
    return locations


def build_url(
    region: str, district: str, suburb_ids: list[str], filters: list[tuple[str, object]]
) -> str:
    """e.g. ROOT_URL/auckland/manukau-city/search?suburb=3756&suburb=3278
           &price_min=50&price_max=500&bedrooms_min=1&bedrooms_max=3
           &bathrooms_min=1&bathrooms_max=2&property_type=house
           &property_type=unit

    The region and district go in the path as slugs, and each suburb is its
    own repeated 'suburb' parameter, as the site does it. The filters follow
    the suburbs, in the order given. Property types repeat the same key.
    """
    params = [("suburb", sid) for sid in suburb_ids] + filters
    return f"{ROOT_URL}/{region}/{district}/search?{urlencode(params)}"


# -------------------------------------------------------------- prompts

def print_options(title: str, options: list[str]) -> None:
    print(f"\n{title}")
    print("-" * 30)
    for i, name in enumerate(options, start=1):
        print(f"{i:>2}. {name}")


def pick_one(title: str, options: list[str]) -> str:
    """Let the user pick one option by number. A single option is auto-picked."""
    if len(options) == 1:
        print(f"\n{title}: {options[0]} (only one on file)")
        return options[0]

    print_options(title, options)
    while True:
        answer = input(f"\nEnter a number (1-{len(options)}): ").strip()
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return options[int(answer) - 1]
        print("Invalid choice, try again.")


def parse_selection(text: str, count: int) -> list[int] | None:
    """Turn '1,3,5-7' or 'all' into 0-based indexes, or None if invalid."""
    text = text.strip().lower()
    if text in ("a", "all"):
        return list(range(count))

    picked: list[int] = []
    for part in text.replace(" ", "").split(","):
        if re.fullmatch(r"\d+", part):
            start = end = int(part)
        elif match := re.fullmatch(r"(\d+)-(\d+)", part):
            start, end = int(match[1]), int(match[2])
        else:
            return None
        if not 1 <= start <= end <= count:
            return None
        picked += [i for i in range(start - 1, end) if i not in picked]
    return picked


def pick_many(title: str, options: list[str]) -> list[int]:
    """Let the user pick several options. Returns their 0-based indexes."""
    print_options(title, options)
    print("\nPick one or more, e.g. 1,3,5-7 (or 'all')")
    while True:
        picked = parse_selection(input("Suburbs: "), len(options))
        if picked:
            return picked
        print(f"Invalid selection, use numbers from 1 to {len(options)}.")


def ask_int(prompt: str, low: int, high: int | None = None) -> int | None:
    """Ask for a whole number from low to high. Blank skips the filter (None)."""
    limit = f"{low}-{high}" if high is not None else f"{low} or more"
    while True:
        answer = input(f"{prompt} ({limit}, Enter to skip): ").strip()
        if not answer:
            return None
        if answer.isdigit() and int(answer) >= low and (high is None or int(answer) <= high):
            return int(answer)
        print(f"Enter a whole number, {limit}, or press Enter to skip.")


def ask_range(
    label: str, key: str, low: int, high: int | None = None
) -> list[tuple[str, int]]:
    """Ask for an optional min and max, re-asking if min is above max.
    Returns [(key_min, n), (key_max, n)], leaving out whatever was skipped."""
    while True:
        lowest = ask_int(f"Min {label}", low, high)
        highest = ask_int(f"Max {label}", low, high)
        if lowest is None or highest is None or lowest <= highest:
            break
        print(f"Min {label} can't be higher than max {label}, try again.")

    pairs = [(f"{key}_min", lowest), (f"{key}_max", highest)]
    return [(name, value) for name, value in pairs if value is not None]


def ask_property_types() -> list[tuple[str, str]]:
    """Pick one or more property types. Blank means any (no parameter)."""
    print_options("Property types", [t.title() for t in PROPERTY_TYPES])
    print("\nPick one or more, e.g. 1,3 (Enter for any)")
    while True:
        answer = input("Types: ").strip()
        if not answer:
            return []
        picked = parse_selection(answer, len(PROPERTY_TYPES))
        if picked:
            # 'car park' -> 'car-park'
            return [("property_type", PROPERTY_TYPES[i].replace(" ", "-")) for i in picked]
        print(f"Invalid selection, use numbers from 1 to {len(PROPERTY_TYPES)}.")


def ask_filters() -> list[tuple[str, object]]:
    """Ask for price, bedrooms, bathrooms and property types, in the order they
    appear in the URL. Anything skipped is left out."""
    print("\nFilters (press Enter to skip any of them)")
    return [
        *ask_range("weekly price", "price", 0),
        *ask_range("bedrooms", "bedrooms", 0, 6),
        *ask_range("bathrooms", "bathrooms", 0, 6),
        *ask_property_types(),
    ]


# ---------------------------------------------------------------- main

def main() -> None:
    locations = load_locations()

    region = pick_one("Regions on file", list(locations))
    district = pick_one(f"Districts in {region}", list(locations[region]))

    suburbs = locations[region][district]
    picked = pick_many(f"Suburbs in {district}", [name for name, _ in suburbs])
    chosen = [suburbs[i] for i in picked]

    filters = ask_filters()

    print(f"\n{region} / {district}: {', '.join(name for name, _ in chosen)}")
    print(build_url(region, district, [sid for _, sid in chosen], filters))


if __name__ == "__main__":
    main()