"""Saved suburbs (searches.csv) and results-URL building. From build_url.py and
grabber_skill.py, with the prompts taken out."""

import csv
from urllib.parse import urlencode

import config

CSV_COLUMNS = ["region", "district", "suburb_name", "suburb_id"]
PROPERTY_TYPES = ["apartment", "car-park", "house", "townhouse", "unit"]


def load_locations() -> dict[str, dict[str, list[tuple[str, str]]]]:
    """region -> district -> [(suburb name, suburb id), ...]"""
    locations: dict[str, dict[str, list[tuple[str, str]]]] = {}
    if not config.SEARCHES_CSV.exists():
        return locations
    with config.SEARCHES_CSV.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            suburbs = locations.setdefault(row["region"], {}).setdefault(row["district"], [])
            entry = (row["suburb_name"], row["suburb_id"])
            if entry not in suburbs:
                suburbs.append(entry)
    return locations


def locations_as_json() -> list[dict]:
    return [
        {
            "region": region,
            "districts": [
                {"district": district,
                 "suburbs": [{"id": sid, "name": name} for name, sid in suburbs]}
                for district, suburbs in districts.items()
            ],
        }
        for region, districts in load_locations().items()
    ]


def save_suburbs(region: str, district: str, suburbs: list[tuple[str, str]]) -> int:
    """Append suburbs (name, id) not already saved. Returns how many were added."""
    known: set[str] = set()
    if config.SEARCHES_CSV.exists():
        with config.SEARCHES_CSV.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames != CSV_COLUMNS:
                raise ValueError(f"{config.SEARCHES_CSV} has unexpected columns: {reader.fieldnames}")
            known = {row["suburb_id"] for row in reader}

    new = [(name, sid) for name, sid in suburbs if sid not in known]
    if not new:
        return 0
    write_header = not config.SEARCHES_CSV.exists()
    with config.SEARCHES_CSV.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        if write_header:
            writer.writeheader()
        for name, sid in new:
            writer.writerow({"region": region, "district": district,
                             "suburb_name": name, "suburb_id": sid})
    return len(new)


def build_url(region: str, district: str, suburb_ids: list[str],
              filters: list[tuple[str, object]]) -> str:
    """ROOT/<region>/<district>/search?suburb=..&suburb=..&price_min=..&..."""
    params = [("suburb", sid) for sid in suburb_ids] + filters
    return f"{config.ROOT_URL}/{region}/{district}/search?{urlencode(params)}"
