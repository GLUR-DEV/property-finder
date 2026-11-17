"""Chart data for the statistics page, computed from the saved search history.

Snapshot charts use the latest run of the chosen search. Trend charts compare
every run of that same search."""

import math
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta

import history

HIST_MAX_BINS = 12
TOP_SUBURBS = 15
TOP_AGENCIES = 8
TREND_SUBURBS = 5
WEEKS_SHOWN = 12
AGE_BUCKETS = [("0 to 7 days", 7), ("8 to 14 days", 14), ("15 to 30 days", 30),
               ("31 to 60 days", 60), ("61 to 90 days", 90), ("Over 90 days", None)]


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 2)


def _median(values: list[float]) -> float | None:
    return _round(statistics.median(values)) if values else None


def _prices(rows: list[dict]) -> list[float]:
    return [r["rent_per_week"] for r in rows if r["rent_per_week"] and r["rent_per_week"] > 0]


def _week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())  # Monday


def _bedroom_label(n: int) -> str:
    return "Studio" if n == 0 else f"{n} bedroom" + ("" if n == 1 else "s")


# ------------------------------------------------------------- rent

def histogram(prices: list[float]) -> list[dict]:
    if not prices:
        return []
    low, high = min(prices), max(prices)
    width = next((w for w in (5, 10, 20, 25, 50, 100, 200, 250, 500) if (high - low) / w <= HIST_MAX_BINS), 1000)
    start = math.floor(low / width) * width
    bins, edge = [], start
    while edge <= high:
        bins.append({"from": edge, "to": edge + width, "count": 0})
        edge += width
    for price in prices:
        bins[int((price - start) // width)]["count"] += 1
    return bins


def rent_summary(rows: list[dict]) -> dict:
    prices = _prices(rows)
    return {
        "median": _median(prices),
        "mean": _round(statistics.fmean(prices)) if prices else None,
        "min": min(prices) if prices else None,
        "max": max(prices) if prices else None,
        "histogram": histogram(prices),
    }


def by_suburb(rows: list[dict]) -> list[dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r["suburb"] and r["rent_per_week"]:
            groups[r["suburb"]].append(r)
    busiest = sorted(groups.items(), key=lambda kv: -len(kv[1]))[:TOP_SUBURBS]
    out = [{
        "suburb": name,
        "count": len(group),
        "median": _median(_prices(group)),
        # a studio counts as one bedroom so the division is always defined
        "perBedroom": _median([r["rent_per_week"] / max(r["bedrooms"] or 0, 1) for r in group]),
    } for name, group in busiest]
    return sorted(out, key=lambda s: -(s["median"] or 0))


def group_stats(rows: list[dict], key, label, order) -> list[dict]:
    groups: dict[object, list[float]] = defaultdict(list)
    for r in rows:
        k = key(r)
        if k is not None and r["rent_per_week"]:
            groups[k].append(r["rent_per_week"])
    return [{"label": label(k), "count": len(v), "min": min(v), "median": _median(v), "max": max(v)}
            for k, v in sorted(groups.items(), key=lambda kv: order(kv[0], kv[1]))]


# ----------------------------------------------------------- supply

def supply(rows: list[dict], now: datetime) -> dict:
    this_week = _week_start(now.date())

    # New listings per week, for the last few weeks
    first_week = this_week - timedelta(weeks=WEEKS_SHOWN - 1)
    started = [t.date() for t in (history.parse_time(r["start_date"]) for r in rows) if t]
    new_per_week = [{"weekStart": (first_week + timedelta(weeks=i)).isoformat(), "count": 0}
                    for i in range(WEEKS_SHOWN)]
    older = 0
    for day in started:
        index = (_week_start(day) - first_week).days // 7
        if index < 0:
            older += 1
        elif index < WEEKS_SHOWN:
            new_per_week[index]["count"] += 1

    # When places become available: before this week, the next eight weeks, later
    available = [date.fromisoformat(r["available_from"]) for r in rows if r["available_from"]]
    weeks = [{"weekStart": (this_week + timedelta(weeks=i)).isoformat(), "count": 0} for i in range(8)]
    past = later = 0
    for day in available:
        index = (_week_start(day) - this_week).days // 7
        if index < 0:
            past += 1
        elif index >= len(weeks):
            later += 1
        else:
            weeks[index]["count"] += 1

    # How long the current listings have been up
    ages = [(now - t).days for t in (history.parse_time(r["start_date"]) for r in rows) if t]
    buckets = [{"label": label, "count": 0} for label, _ in AGE_BUCKETS]
    for age in ages:
        for bucket, (_, limit) in zip(buckets, AGE_BUCKETS):
            if limit is None or age <= limit:
                bucket["count"] += 1
                break

    return {
        "newPerWeek": new_per_week, "newOlder": older,
        "availability": weeks, "availablePast": past, "availableLater": later,
        "ages": buckets, "medianAgeDays": _median(ages),
    }


# ----------------------------------------------------------- listers

def listers(rows: list[dict]) -> dict:
    def group(members: list[dict]) -> dict:
        return {"count": len(members),
                "share": round(len(members) / len(rows), 4) if rows else 0,
                "medianRent": _median(_prices(members))}

    agencies: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r["is_agency"]:
            agencies[r["agency_name"] or "Unnamed"].append(r)
    top = sorted(agencies.items(), key=lambda kv: -len(kv[1]))[:TOP_AGENCIES]

    return {
        "privateListers": group([r for r in rows if not r["is_agency"]]),
        "agencyListers": group([r for r in rows if r["is_agency"]]),
        "topAgencies": [{"name": n, "count": len(m), "medianRent": _median(_prices(m))} for n, m in top],
    }


# ------------------------------------------------------------ trends

def trends(runs: list[dict]) -> dict:
    points, previous = [], None
    for run in runs:
        rows = run["rows"]
        point = {
            "searchedAt": run["searchedAt"].isoformat(timespec="seconds"),
            "count": len(rows),
            "medianRent": _median(_prices(rows)),
            "meanRent": _round(statistics.fmean(_prices(rows))) if _prices(rows) else None,
            "privateShare": round(sum(1 for r in rows if not r["is_agency"]) / len(rows), 4) if rows else 0,
            "new": None, "removed": None, "priceChanged": None, "removedMedianDaysListed": None,
        }
        if previous:
            now = {r["listing_id"]: r for r in rows}
            before = {r["listing_id"]: r for r in previous["rows"]}
            gone = [before[i] for i in before if i not in now]
            point["new"] = sum(1 for i in now if i not in before)
            point["removed"] = len(gone)
            point["priceChanged"] = sum(
                1 for i in now if i in before
                and now[i]["rent_per_week"] is not None and before[i]["rent_per_week"] is not None
                and now[i]["rent_per_week"] != before[i]["rent_per_week"])
            # Last seen in the previous run, so that is the latest it could have been up until
            days = [(previous["searchedAt"] - t).days
                    for t in (history.parse_time(r["start_date"]) for r in gone) if t]
            point["removedMedianDaysListed"] = _median(days)
        points.append(point)
        previous = run

    def series(names: list, key, label) -> list[dict]:
        return [{"name": label(n),
                 "values": [_median(_prices([r for r in run["rows"] if key(r) == n])) for run in runs]}
                for n in names]

    all_rows = [r for run in runs for r in run["rows"]]
    bedrooms = sorted({r["bedrooms"] for r in all_rows if r["bedrooms"] is not None})
    suburb_counts: dict[str, int] = defaultdict(int)
    for r in all_rows:
        if r["suburb"]:
            suburb_counts[r["suburb"]] += 1
    top_suburbs = [s for s, _ in sorted(suburb_counts.items(), key=lambda kv: -kv[1])[:TREND_SUBURBS]]

    return {
        "runs": points,
        "byBedrooms": series(bedrooms, lambda r: r["bedrooms"], _bedroom_label),
        "bySuburb": series(top_suburbs, lambda r: r["suburb"], lambda s: s),
    }


# -------------------------------------------------------------- main

def build(scope_key: str | None = None) -> dict:
    scopes = history.scopes()
    if not scopes:
        return {"hasData": False, "scopes": []}

    scope = next((s for s in scopes if s["scopeKey"] == scope_key), scopes[0])
    runs = history.runs(scope["scopeKey"])
    latest = runs[-1]
    rows, now = latest["rows"], latest["searchedAt"]

    return {
        "hasData": True,
        "scopes": scopes,
        "scope": scope,
        "searchedAt": now.isoformat(timespec="seconds"),
        "count": len(rows),
        "rent": rent_summary(rows),
        "bySuburb": by_suburb(rows),
        "byBedrooms": group_stats(rows, lambda r: r["bedrooms"], _bedroom_label, lambda k, v: k),
        "byType": group_stats(rows, lambda r: r["property_type"], lambda k: k, lambda k, v: -len(v)),
        "supply": supply(rows, now),
        "listers": listers(rows),
        "trends": trends(runs),
    }
