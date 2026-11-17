"""Save and load the latest search's listings (JSON for the app, CSV for you)."""

import csv
import json
from datetime import datetime, timezone

import config


def flatten(obj: dict, prefix: str = "") -> dict:
    """{'a': {'b': 1}} -> {'a.b': 1}. Lists are kept as JSON text in one cell."""
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


def save(url: str, listings: list[dict]) -> dict:
    result = {
        "searchedAt": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "url": url,
        "count": len(listings),
        "listings": listings,
    }
    config.LISTINGS_JSON.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    rows = [flatten(item) for item in listings]
    columns = list(dict.fromkeys(key for row in rows for key in row))
    with config.LISTINGS_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    return result


def load() -> dict:
    if not config.LISTINGS_JSON.exists():
        return {"searchedAt": None, "url": None, "count": 0, "listings": []}
    return json.loads(config.LISTINGS_JSON.read_text(encoding="utf-8"))
