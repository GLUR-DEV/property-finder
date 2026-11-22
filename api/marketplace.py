"""Facebook Marketplace rentals, captured by the Chrome extension in extension/.

The extension sends two things:
  cards    the label text of each listing link on the Marketplace rentals page
  details  what a listing's own page shows, read from a tab the extension opened

Listings are kept in marketplace.json, apart from the Trade Me results, so a Trade Me
search or watch run never wipes them. get_listings() in main.py adds them to the
latest search, so the Listings and Map pages show both. A listing drops off 14 days
after the extension last saw it on the rentals page (MARKETPLACE_KEEP_DAYS).

Facebook listing IDs are 17 digits, more than JavaScript numbers hold exactly, so
every ID that goes back to the extension is a string.
"""

import json
import os
import re
import threading
import urllib.request
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlparse

import config
import history
import locations

SOURCE = "facebook"
ITEM_URL = "https://www.facebook.com/marketplace/item/{}/"
IMAGE_ROUTE = "/api/marketplace/images/"
IMAGE_NAME = re.compile(r"^\d{1,20}_\d{1,2}\.(?:jpg|webp|png)$")
IMAGE_TYPES = {"image/jpeg": "jpg", "image/webp": "webp", "image/png": "png"}
MAX_IMAGE_BYTES = 3_000_000
GONE_RETRY_DAYS = 2   # a listing whose page said "not available" is tried again after this

_lock = threading.RLock()


# ------------------------------------------------------------ time

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(when: datetime) -> str:
    return when.astimezone(timezone.utc).isoformat(timespec="seconds")


# ------------------------------------------------------------ cards

# "2 beds 1 bath Flat/apartment, NZ$575, Auckland, New Zealand, listing 24954888290801094"
# The title can hold commas and line breaks, so the fixed parts are matched from the end.
CARD_RE = re.compile(
    r"^(?:Just listed, )?(?P<title>.*?), (?P<price>NZ\$[\d,]+(?:\.\d+)?|FREE)"
    r"(?:, reduced from NZ\$[\d,]+(?:\.\d+)?)?, (?P<place>.+?), New Zealand, listing (?P<id>\d+)\s*$",
    re.S)

# Things that are not a place to live. HARD matches are dropped whatever else the title says;
# SOFT ones (car parks, garages...) are dropped unless the title also names a home.
#
# Landlords write "looking for a tenant" and "flatmate wanted" in real rental ads, so only wording
# that means the poster wants to rent a place, or is selling one, is dropped. A junk ad that slips
# through only costs one opened page; a real rental dropped here is lost.
HARD_JUNK = re.compile(
    r"\b(not for rent|for sale|developers? sale|ready to sell|"
    r"looking (?:for|to) (?:a )?(?:rent|rental|room|flat|house|place|home|unit|apartment)\b|"
    r"(?:room|flat|house|rental|place|home)s? wanted|wanted:? (?:to rent|a room|a flat|a house|rental))", re.I)
SOFT_JUNK = re.compile(
    r"\b(car ?parks?|carports?|garages?|storage|curtains?|blinds?|commercial|warehouse|kitchen)\b", re.I)
HOME_WORDS = re.compile(
    r"\b(beds?|bedrooms?|rooms?|house|home|unit|apartment|flat|studio|townhouse|cabin|ensuite|sublet|villa)\b",
    re.I)


def parse_card(label: str) -> dict | None:
    m = CARD_RE.match((label or "").strip())
    if m is None:
        return None
    price = m["price"]
    return {
        "id": int(m["id"]),
        "title": m["title"].strip(),
        "amount": None if price == "FREE" else float(price[3:].replace(",", "")),
        "place": m["place"].strip(),
    }


def skip_reason(title: str, amount: float | None) -> str | None:
    """Why a card isn't worth opening, or None if it should be."""
    if amount is None:
        return "no price"
    if amount < 20 or int(amount) in config.MARKETPLACE_PLACEHOLDER_PRICES:
        return "placeholder price"
    if amount > config.MARKETPLACE_MAX_PRICE:
        return "price too high"
    if HARD_JUNK.search(title):
        return "not a rental"
    if SOFT_JUNK.search(title) and not HOME_WORDS.search(title):
        return "not a home"
    return None


# ------------------------------------------------------------ price

PRICE_RE = re.compile(
    r"^(?:NZ)?\$\s?(?P<amount>[\d,]+(?:\.\d+)?)(?:\s*(?:/|per\s+|a\s+)\s*(?P<period>[A-Za-z]+)|\s+(?P<word>monthly|weekly)\b)?",
    re.I)
PERIODS = {"month": "month", "monthly": "month", "mo": "month", "mth": "month",
           "week": "week", "weekly": "week", "wk": "week", "w": "week",
           "day": "day", "night": "day", "year": "year", "yr": "year"}


def parse_price(text: str) -> tuple[float | None, str | None]:
    """'NZ$690/month' -> (690.0, 'month'). The period is None when none is shown."""
    m = PRICE_RE.match((text or "").strip())
    if m is None:
        return None, None
    return float(m["amount"].replace(",", "")), PERIODS.get((m["period"] or m["word"] or "").lower())


def says_weekly(description: str, amount: float) -> bool:
    """The description gives this amount as a weekly price ('Price per week $220', '$220 pw')."""
    weekly = r"(?:per\s*(?:week|wk)|\bp/?w\b|weekly|/\s*(?:week|wk)|a week)"
    for number in {f"{amount:,.0f}", f"{amount:.0f}"}:
        n = re.escape(number)
        if re.search(rf"\$\s?{n}\b[^.\n]{{0,25}}?{weekly}|{weekly}[^.\n$]{{0,15}}\$\s?{n}\b",
                     description, re.I):
            return True
    return False


def weekly_rent(amount: float | None, period: str | None, is_room: bool,
                description: str) -> tuple[float | None, str | None]:
    """The rent per week, and a note when the listed price had to be interpreted.

    Marketplace sellers often leave the price period on its default, so '$690/month' for a
    3 bedroom unit is really $690 a week. A monthly price is believed unless the description
    says it is weekly, or the weekly figure is too low to be a real rent for that kind of place."""
    if amount is None:
        return None, None
    if period == "week":
        return amount, None
    if period is None:
        return amount, "No price period was shown, so it was treated as per week."
    if period == "day":
        return round(amount * 7, 2), f"Converted from ${amount:,.0f} per day."
    if period == "year":
        return round(amount / 52, 2), f"Converted from ${amount:,.0f} per year."

    if says_weekly(description, amount):
        return amount, "Listed as per month, but the description says per week, so it was treated as per week."
    converted = round(amount * 12 / 52, 2)
    floor = config.MARKETPLACE_MIN_WEEKLY_ROOM if is_room else config.MARKETPLACE_MIN_WEEKLY_HOME
    if converted < floor:
        return amount, (f"Listed as ${amount:,.0f} per month, which would be ${converted:,.0f} a week "
                        "and looks too low, so it was treated as per week.")
    return converted, f"Converted from ${amount:,.0f} per month."


# ------------------------------------------------------------ details

ROOM_RE = re.compile(
    r"\b(room only|rooms? (?:for|to) rent|rooms? available|rooms? in|single room|double room|"
    r"master ?(?:bed)?room|ensuite|sublet|flatmate|shared (?:house|flat|living|accommodation))\b|"
    r"\b(?:big|large|spacious|furnished|private)\s+room\b", re.I)
KIND_WORDS = re.compile(r"\b(apartment|flat|townhouse|house|unit|studio|villa)\b", re.I)
BED_RE = re.compile(r"\b(\d{1,2})\s*(?:bed(?:room)?s?|br)\b", re.I)
BATH_RE = re.compile(r"\b(\d{1,2})\s*bath(?:room)?s?\b", re.I)


def _clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


KIND_TEXT = re.compile(r"^[A-Za-z]+(?:[/ -][A-Za-z]+){0,2}$")   # 'House', 'Flat/apartment', 'Room only'


def card_facts(title: str) -> dict:
    """Structured card titles read '2 beds 1 bath Flat/apartment' or 'Studio 1 bath House'.

    Free-text titles start the same way ('3 Bedrooms unit with garage...', '1 bed 2 bathrooms - Room
    only'), so the word ends are checked and the rest only counts as a type if it looks like one."""
    m = re.match(r"^(?:(\d+) beds?\b|(Studio)\b)(?: (\d+) baths?\b)? ?(.*)$", title or "", re.I)
    if m is None:
        return {"beds": None, "baths": None, "type": None}
    rest = m[4].strip()
    return {"beds": 0 if m[2] else int(m[1]), "baths": int(m[3]) if m[3] else None,
            "type": rest if KIND_TEXT.match(rest) else None}


def unit_details(items: list[str]) -> dict:
    """The 'Unit details' block: ['House', '1 bed · 2 baths', 'Central AC', 'Parking available']."""
    out: dict = {"type": None, "beds": None, "baths": None, "amenities": [], "parking": None}
    for i, raw in enumerate(items):
        item = _clean(raw)
        if not item:
            continue
        if re.search(r"\b(?:beds?|baths?|studio)\b", item, re.I) and re.search(r"\d|studio", item, re.I):
            bed, bath = BED_RE.search(item), BATH_RE.search(item)
            out["beds"] = 0 if re.search(r"\bstudio\b", item, re.I) and not bed else (int(bed[1]) if bed else None)
            out["baths"] = int(bath[1]) if bath else None
        elif out["type"] is None and (i == 0 or KIND_WORDS.fullmatch(item)
                                      or item.lower() in ("flat/apartment", "room", "cabin")):
            out["type"] = item
        else:
            out["amenities"].append(item)
            if out["parking"] is None and re.search(r"parking|garage|carport", item, re.I):
                out["parking"] = item
    return out


def property_kind(title: str, listed_type: str | None) -> str | None:
    if ROOM_RE.search(title):
        return "Room"
    if re.search(r"\bcabin\b", title, re.I):
        return "Cabin"
    if listed_type:
        return listed_type
    m = KIND_WORDS.search(title)
    return m[1].title() if m else None


def _count(pattern: re.Pattern, *texts: str, limit: int = 12) -> int | None:
    for text in texts:
        m = pattern.search(text)
        if m and int(m[1]) <= limit:
            return int(m[1])
    return None


# ---- dates

UNITS = {"minute": timedelta(minutes=1), "min": timedelta(minutes=1), "hour": timedelta(hours=1),
         "hr": timedelta(hours=1), "day": timedelta(days=1), "week": timedelta(weeks=1),
         "month": timedelta(days=30)}
MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def _listed_at(text: str, now: datetime) -> datetime | None:
    """'Listed over a week ago' -> about 8 days before now. Facebook only gives rough ages."""
    s = text.lower()
    if re.search(r"\bjust (?:now|listed)\b|\bmoments? ago\b|\btoday\b", s):
        return now
    if "yesterday" in s:
        return now - timedelta(days=1)
    m = re.search(r"\b(?:(\d+)|an?|one)\s+(minute|min|hour|hr|day|week|month)s?\s+ago\b", s)
    if m is None:
        return None
    when = now - (int(m[1]) if m[1] else 1) * UNITS[m[2]]
    return when - timedelta(days=1) if re.search(r"\bover\b|\bmore than\b", s) else when


def _parse_date(text: str, today: date) -> date | None:
    m = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", text)           # 15/03/2027, d/m as in NZ
    if m:
        day, month, year = int(m[1]), int(m[2]), m[3]
    else:
        m = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?([A-Za-z]{3,9})\b(?:,?\s+(\d{4}))?", text)
        if m and m[2][:3].lower() in MONTHS:                                  # 15th of March
            day, month, year = int(m[1]), MONTHS[m[2][:3].lower()], m[3]
        else:
            m = re.search(r"\b([A-Za-z]{3,9})\s+(\d{1,2})(?:st|nd|rd|th)?\b(?:,?\s+(\d{4}))?", text)
            if not m or m[1][:3].lower() not in MONTHS:                       # March 15
                return None
            day, month, year = int(m[2]), MONTHS[m[1][:3].lower()], m[3]
    try:
        if year:
            return date(int(year) + (2000 if int(year) < 100 else 0), month, day)
        found = date(today.year, month, day)
        return found if found >= today - timedelta(days=30) else date(today.year + 1, month, day)
    except ValueError:
        return None


def listed_and_available(text: str, now: datetime) -> tuple[datetime | None, str | None]:
    """'Listed over a week ago · Available now' -> (about when it was listed, 'Available now')."""
    listed, available = None, None
    for part in re.split(r"\s*[·•|]\s*", text or ""):
        part = part.strip()
        if re.match(r"(?:just )?listed\b", part, re.I):
            listed = _listed_at(part, now)
        elif re.match(r"available\b", part, re.I):
            available = part
    return listed, available


def available_from(text: str | None, now: datetime) -> str | None:
    """'Available from 5 December' -> '5/12/2026'. 'Available now' (or anything unreadable) -> None,
    which the pages already treat as available now."""
    rest = re.sub(r"^available\s*(?:from|on|after|by)?\s*", "", text or "", flags=re.I).strip()
    if not rest or re.match(r"(?:now|immediately|today|asap)\b", rest, re.I):
        return None
    found = _parse_date(rest, now.date())
    return f"{found.day}/{found.month}/{found.year}" if found else None


# ---- place

def _pretty(slug: str | None) -> str | None:
    return slug.replace("-", " ").title() if slug else None


def find_suburb(place: str, texts: list[str], region_slug: str) -> tuple[str | None, str | None, str | None]:
    """(suburb, district slug, region slug) of a suburb you've saved that the listing mentions.

    Looks in each text in turn (title first), at the earliest mention. Facebook itself only
    says 'Auckland', so this is a best guess from the words in the listing; None if nothing matches."""
    saved = locations.load_locations()
    regions = [region_slug] if region_slug in saved else list(saved)
    names = [(name, district, region) for region in regions
             for district, suburbs in saved[region].items() for name, _ in suburbs]
    for text in [place, *texts]:
        found = None
        for name, district, region in names:
            m = re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text or "", re.I)
            if m and (found is None or (m.start(), -len(name)) < found[0]):
                found = ((m.start(), -len(name)), name, district, region)
        if found:
            return found[1], found[2], found[3]
    return None, None, None


def normalise(d: dict, now: datetime, photos: list[str], prev: dict) -> dict:
    """A Marketplace detail payload as a listing in the shape the pages already read."""
    card = parse_card(d.get("card_label") or "")
    title = _clean(d.get("title")) or _clean(card["title"] if card else "")
    description = (d.get("description") or "").strip()
    facts = unit_details(d.get("details") or [])
    from_card = card_facts(card["title"] if card else "")

    kind = property_kind(title, facts["type"] or from_card["type"])
    beds = next((v for v in (facts["beds"], from_card["beds"]) if v is not None), None)
    if beds is None:
        beds = _count(BED_RE, title, description)
    if beds is None and re.search(r"\bstudio\b", title, re.I):
        beds = 0
    baths = next((v for v in (facts["baths"], from_card["baths"]) if v is not None), None)
    if baths is None:
        baths = _count(BATH_RE, title, description)

    amount, period = parse_price(d.get("price_text") or "")
    if amount is None and card:
        amount, period = card["amount"], None
    rent, note = weekly_rent(amount, period, kind == "Room", description)

    region_slug = _clean(d.get("region_slug")).lower()
    place = card["place"] if card else ""
    suburb, district_slug, matched = find_suburb(place, [title, description, _clean(d.get("location"))],
                                                 region_slug)
    region_name = _pretty(matched or region_slug)
    if suburb is None and place and place.lower() not in (region_name or "").lower():
        suburb = place    # a suburb-level place straight from Facebook, like Takanini

    listed, available_raw = listed_and_available(d.get("listed_text") or "", now)
    address = re.search(r"\baddress\s*(?:is|:)\s*([^\n.]{5,80})", description, re.I)
    listing_id = int(d["id"])

    return {
        "listingId": listing_id,
        "listingSource": SOURCE,
        "externalUrl": ITEM_URL.format(listing_id),
        "title": title,
        "address": address[1].strip() if address else None,
        "suburb": suburb,
        "district": _pretty(district_slug),
        "region": region_name,
        "propertyType": kind,
        "rentPerWeek": rent,
        "priceDisplay": _clean(d.get("price_text")) or None,
        "priceNote": note,
        "bedrooms": beds,
        "bathrooms": baths,
        "parking": facts["parking"],
        "availableFrom": available_from(available_raw, now),
        "availableText": available_raw,
        "startDate": prev.get("startDate") or _iso(listed or now),   # a first estimate; kept after that
        "pictureHref": photos[0] if photos else None,
        "photoUrls": photos,
        "amenities": facts["amenities"],
        "description": description[:4000],
        "agency": None,
        "geographicLocation": None,
    }


# ------------------------------------------------------------ photos

def _download(url: str, listing_id: int, number: int) -> str | None:
    """Save a photo locally. Facebook's photo links stop working after a few days, and a listing
    stays for two weeks, so the pages show our own copy."""
    if not _is_fbcdn(url):
        return None
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            extension = IMAGE_TYPES.get(response.headers.get_content_type())
            body = response.read(MAX_IMAGE_BYTES + 1)
    except (OSError, ValueError):
        return None
    if extension is None or not body or len(body) > MAX_IMAGE_BYTES:
        return None
    config.MARKETPLACE_IMAGES.mkdir(parents=True, exist_ok=True)
    name = f"{listing_id}_{number}.{extension}"
    (config.MARKETPLACE_IMAGES / name).write_bytes(body)
    return name


def _is_fbcdn(url: str) -> bool:
    parts = urlparse(url)
    return parts.scheme == "https" and (parts.hostname or "").endswith(".fbcdn.net")


def save_photos(listing_id: int, urls: list[str]) -> list[str]:
    """Paths (under /api/marketplace/images/) of the saved photos. A photo that couldn't be saved
    keeps Facebook's own link, which works for a while, so the listing still has a picture."""
    saved: list[str] = []
    seen: set[str] = set()
    for url in urls:
        key = urlparse(url).path
        if key in seen:
            continue
        seen.add(key)
        if len(saved) >= config.MARKETPLACE_MAX_PHOTOS:
            break
        name = _download(url, listing_id, len(saved))
        if name:
            saved.append(IMAGE_ROUTE + name)
        elif _is_fbcdn(url):
            saved.append(url)
    return saved


def image_path(name: str):
    """The saved photo for a /api/marketplace/images/<name> request, or None."""
    if not IMAGE_NAME.match(name):
        return None
    path = config.MARKETPLACE_IMAGES / name
    return path if path.is_file() else None


def _delete_photos(key: str) -> None:
    if config.MARKETPLACE_IMAGES.exists():
        for path in config.MARKETPLACE_IMAGES.glob(f"{key}_*"):
            path.unlink(missing_ok=True)


# ------------------------------------------------------------ store

def _load() -> dict:
    if not config.MARKETPLACE_JSON.exists():
        return {}
    try:
        return json.loads(config.MARKETPLACE_JSON.read_text(encoding="utf-8"))
    except ValueError:
        # Keep the unreadable file rather than overwrite it with an empty one
        config.MARKETPLACE_JSON.replace(config.MARKETPLACE_JSON.with_suffix(".json.broken"))
        return {}


def _save(data: dict) -> None:
    temp = config.MARKETPLACE_JSON.with_suffix(".json.tmp")
    temp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, config.MARKETPLACE_JSON)


def _expired(record: dict, now: datetime) -> bool:
    last = history.parse_time(record.get("_lastSeen"))
    return last is None or now - last > timedelta(days=config.MARKETPLACE_KEEP_DAYS)


def _prune(data: dict, now: datetime) -> None:
    for key in [k for k, rec in data.items() if _expired(rec, now)]:
        del data[key]
        _delete_photos(key)


def ingest_cards(labels: list[str]) -> dict:
    """Take the listing links seen on the rentals page. Listings we already have are marked as
    seen again; the rest are returned as IDs (strings) for the extension to open and read."""
    now = _now()
    seen = unreadable = known = 0
    skipped: dict[str, int] = {}
    need: list[str] = []
    with _lock:
        data = _load()
        _prune(data, now)
        for label in dict.fromkeys(labels):
            card = parse_card(label)
            if card is None:
                unreadable += 1
                continue
            seen += 1
            reason = skip_reason(card["title"], card["amount"])
            if reason:
                skipped[reason] = skipped.get(reason, 0) + 1
                continue
            key = str(card["id"])
            record = data.get(key)
            if record and record.get("_gone"):
                gone_at = history.parse_time(record.get("_goneAt"))
                if gone_at and now - gone_at < timedelta(days=GONE_RETRY_DAYS):
                    known += 1
                    continue
                record = None
            if record:
                record["_lastSeen"] = _iso(now)
                known += 1
            else:
                need.append(key)
        _save(data)
    return {"seen": seen, "unreadable": unreadable, "known": known, "skipped": skipped, "need": need}


def ingest_details(d: dict) -> dict:
    """Store what a listing's page showed. d['gone'] means the page said it isn't available."""
    listing_id = int(d["id"])
    key = str(listing_id)
    now = _now()

    if d.get("gone"):
        with _lock:
            data = _load()
            data[key] = {"_gone": True, "_goneAt": _iso(now), "_lastSeen": _iso(now)}
            _save(data)
        return {"stored": False, "gone": True}

    photos = save_photos(listing_id, d.get("photos") or [])   # slow, so before taking the lock
    with _lock:
        data = _load()
        _prune(data, now)
        prev = data.get(key) or {}
        record = normalise(d, now, photos, prev)
        record["_firstSeen"] = prev.get("_firstSeen") or _iso(now)
        record["_lastSeen"] = _iso(now)
        data[key] = record
        _save(data)
    return {"stored": True, "photos": len(photos), "rentPerWeek": record["rentPerWeek"],
            "note": record["priceNote"]}


def listings() -> list[dict]:
    """The listings to show: every one the extension has seen in the last MARKETPLACE_KEEP_DAYS."""
    now = _now()
    out = []
    for record in _load().values():
        if record.get("_gone") or _expired(record, now):
            continue
        listing = {k: v for k, v in record.items() if not k.startswith("_")}
        listing["firstSeenAt"] = record.get("_firstSeen")
        listing["lastSeenAt"] = record.get("_lastSeen")
        out.append(listing)
    return out
