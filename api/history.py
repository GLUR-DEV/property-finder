"""SQLite history of every search, so the stats page can show trends over time.

Each search becomes one row in `searches` plus one row per listing in `listings`
holding only the fields the statistics need. The full detail of the latest search
stays in listings.json.

Runs of the same search (same region, district, suburbs and filters) share a
scope_key, so trends compare like with like."""

import hashlib
import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlparse

import config
import locations

SCHEMA = """
CREATE TABLE IF NOT EXISTS searches (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    searched_at   TEXT    NOT NULL,   -- ISO 8601, UTC
    url           TEXT    NOT NULL,
    scope_key     TEXT    NOT NULL,
    scope_label   TEXT    NOT NULL,
    listing_count INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_searches_scope ON searches (scope_key, searched_at);

CREATE TABLE IF NOT EXISTS listings (
    search_id      INTEGER NOT NULL REFERENCES searches (id) ON DELETE CASCADE,
    listing_id     INTEGER NOT NULL,
    suburb         TEXT,
    district       TEXT,
    property_type  TEXT,
    rent_per_week  REAL,
    bedrooms       INTEGER,
    bathrooms      INTEGER,
    start_date     TEXT,              -- when the listing went live (ISO 8601)
    available_from TEXT,              -- YYYY-MM-DD
    is_agency      INTEGER NOT NULL,  -- 1 = agency or company, 0 = private lister
    agency_name    TEXT,
    is_featured    INTEGER NOT NULL,
    PRIMARY KEY (search_id, listing_id)
);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.HISTORY_DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init() -> None:
    with closing(connect()) as conn, conn:
        conn.executescript(SCHEMA)


# ----------------------------------------------------------- helpers

def parse_time(text: str | None) -> datetime | None:
    """Parse the site's timestamps (e.g. 2026-07-19T22:46:45.020Z) as UTC."""
    if not text:
        return None
    text = text.strip().replace("Z", "+00:00")
    text = re.sub(r"\.(\d+)", lambda m: "." + m.group(1)[:6].ljust(6, "0"), text)
    try:
        value = datetime.fromisoformat(text)
    except ValueError:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _available_from(text: str | None) -> str | None:
    """'19/10/2026' -> '2026-10-19'"""
    try:
        return datetime.strptime(text or "", "%d/%m/%Y").date().isoformat()
    except ValueError:
        return None


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _whole(value: object) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


# ------------------------------------------------------------- scopes

def scope_from_url(url: str) -> dict | None:
    """Region, district, suburbs and filters of a results URL; None if it isn't one."""
    parts = urlparse(url)
    match = re.search(r"/rent/([^/]+)/([^/]+)/search$", parts.path)
    if parts.scheme not in ("http", "https") or not match:
        return None
    query = parse_qsl(parts.query)
    suburbs = sorted({v for k, v in query if k == "suburb"}, key=lambda s: (not s.isdigit(), int(s) if s.isdigit() else 0, s))
    filters = sorted((k, v) for k, v in query if k not in ("suburb", "page"))
    return {"region": match[1], "district": match[2], "suburbs": suburbs, "filters": filters}


def scope_key(scope: dict) -> str:
    return hashlib.sha1(json.dumps(scope, sort_keys=True).encode()).hexdigest()[:12]


def _phrase(noun: str, low: str | None, high: str | None, fmt=str) -> str | None:
    if low is not None and high is not None:
        return f"{noun} {fmt(low)} to {fmt(high)}"
    if high is not None:
        return f"{noun} up to {fmt(high)}"
    if low is not None:
        return f"{noun} from {fmt(low)}"
    return None


def scope_label(scope: dict) -> str:
    """e.g. 'Manukau City, 63 suburbs, rent up to $300, bedrooms up to 2, unit and house'"""
    names = {sid: name for name, sid in locations.load_locations()
             .get(scope["region"], {}).get(scope["district"], [])}
    suburbs = scope["suburbs"]
    where = ", ".join(names.get(s, s) for s in suburbs) if 0 < len(suburbs) <= 3 else f"{len(suburbs)} suburbs"

    f: dict[str, list[str]] = {}
    for key, value in scope["filters"]:
        f.setdefault(key, []).append(value)
    one = lambda key: f[key][0] if key in f else None  # noqa: E731

    bits = [
        scope["district"].replace("-", " ").title(),
        where,
        _phrase("rent", one("price_min"), one("price_max"), lambda v: f"${v}"),
        _phrase("bedrooms", one("bedrooms_min"), one("bedrooms_max")),
        _phrase("bathrooms", one("bathrooms_min"), one("bathrooms_max")),
        " and ".join(t.replace("-", " ") for t in f["property_type"]) if "property_type" in f else None,
    ]
    return ", ".join(b for b in bits if b)


# ------------------------------------------------------------ writing

def _row(listing: dict) -> tuple:
    agency = listing.get("agency")
    return (
        listing["listingId"],
        listing.get("suburb"),
        listing.get("district"),
        listing.get("propertyType"),
        _number(listing.get("rentPerWeek")),
        _whole(listing.get("bedrooms")),
        _whole(listing.get("bathrooms")),
        listing.get("startDate"),
        _available_from(listing.get("availableFrom")),
        1 if agency else 0,
        agency.get("name") if isinstance(agency, dict) else None,
        1 if listing.get("isFeatured") else 0,
    )


def record(url: str, listings: list[dict], searched_at: datetime | None = None) -> int:
    """Save one search and its listings. Returns the new search id."""
    scope = scope_from_url(url)
    key, label = (scope_key(scope), scope_label(scope)) if scope else ("imported", "Imported results")
    when = (searched_at or datetime.now(timezone.utc)).astimezone(timezone.utc)

    rows = {l["listingId"]: _row(l) for l in listings if l.get("listingId") is not None}
    with closing(connect()) as conn, conn:
        search_id = conn.execute(
            "INSERT INTO searches (searched_at, url, scope_key, scope_label, listing_count) VALUES (?,?,?,?,?)",
            (when.isoformat(timespec="seconds"), url, key, label, len(rows)),
        ).lastrowid
        conn.executemany(
            "INSERT INTO listings (search_id, listing_id, suburb, district, property_type, rent_per_week,"
            " bedrooms, bathrooms, start_date, available_from, is_agency, agency_name, is_featured)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(search_id, *row) for row in rows.values()],
        )
    return search_id


def import_existing() -> None:
    """On first run, save the results already in listings.json as the first search."""
    with closing(connect()) as conn:
        if conn.execute("SELECT 1 FROM searches LIMIT 1").fetchone():
            return
    if not config.LISTINGS_JSON.exists():
        return
    data = json.loads(config.LISTINGS_JSON.read_text(encoding="utf-8"))
    if data.get("listings"):
        record(data.get("url") or "", data["listings"], parse_time(data.get("searchedAt")))


# ------------------------------------------------------------ reading

def scopes() -> list[dict]:
    """Every distinct search, most recently run first."""
    with closing(connect()) as conn:
        found = conn.execute(
            "SELECT scope_key, COUNT(*) AS runs, MIN(searched_at) AS first_run, MAX(searched_at) AS last_run"
            " FROM searches GROUP BY scope_key ORDER BY last_run DESC"
        ).fetchall()
        out = []
        for r in found:
            latest = conn.execute(
                "SELECT scope_label, listing_count FROM searches WHERE scope_key = ?"
                " ORDER BY searched_at DESC, id DESC LIMIT 1", (r["scope_key"],)).fetchone()
            out.append({
                "scopeKey": r["scope_key"], "label": latest["scope_label"], "runs": r["runs"],
                "firstRun": r["first_run"], "lastRun": r["last_run"], "lastCount": latest["listing_count"],
            })
        return out


def runs(key: str) -> list[dict]:
    """Every run of a search, oldest first, each with its listings."""
    with closing(connect()) as conn:
        out = []
        for s in conn.execute(
                "SELECT id, searched_at FROM searches WHERE scope_key = ? ORDER BY searched_at, id", (key,)):
            rows = [dict(r) for r in conn.execute("SELECT * FROM listings WHERE search_id = ?", (s["id"],))]
            out.append({"id": s["id"], "searchedAt": parse_time(s["searched_at"]), "rows": rows})
        return out
