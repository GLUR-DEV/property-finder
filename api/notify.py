"""Phone notifications through ntfy (https://ntfy.sh).

Install the free ntfy app on your phone and subscribe to a topic; this module
posts to that topic. On the public server the topic name works like a password,
so the settings live in history.db, which isn't committed, not in the code."""

import json
import re
import urllib.error
import urllib.request
from contextlib import closing
from urllib.parse import urlparse

import history

DEFAULT_SERVER = "https://ntfy.sh"
TOPIC = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class NotifyError(Exception):
    """A notification couldn't be sent."""


# ---------------------------------------------------------- settings

def init() -> None:
    with closing(history.connect()) as conn, conn:
        conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")


def get() -> dict:
    with closing(history.connect()) as conn:
        stored = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings")}
    return {
        "server": stored.get("ntfy.server") or DEFAULT_SERVER,
        "topic": stored.get("ntfy.topic", ""),
        "token": stored.get("ntfy.token", ""),
    }


def public() -> dict:
    """The settings as the web app sees them: the access token is never sent back."""
    s = get()
    return {"server": s["server"], "topic": s["topic"],
            "hasToken": bool(s["token"]), "configured": bool(s["topic"])}


def save(server: str, topic: str, token: str | None) -> dict:
    """token: None keeps the saved one, an empty string removes it."""
    server = server.strip().rstrip("/") or DEFAULT_SERVER
    topic = topic.strip()
    parts = urlparse(server)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError("The server must start with http:// or https://")
    if topic and not TOPIC.match(topic):
        raise ValueError("The topic can only use letters, numbers, - and _ (up to 64 characters).")

    values = {"ntfy.server": server, "ntfy.topic": topic}
    if token is not None:
        values["ntfy.token"] = token.strip()
    with closing(history.connect()) as conn, conn:
        conn.executemany(
            "INSERT INTO settings (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value", values.items())
    return public()


# ----------------------------------------------------------- sending

def send(title: str, message: str, *, click: str | None = None, attach: str | None = None,
         tags: tuple[str, ...] = ("house",), priority: int = 3) -> None:
    s = get()
    if not s["topic"]:
        raise NotifyError("Notifications aren't set up. Choose a topic on the Watches page.")

    # ntfy's JSON publishing keeps accented names (Māngere) intact, unlike HTTP headers
    payload: dict = {"topic": s["topic"], "title": title, "message": message,
                     "tags": list(tags), "priority": priority}
    if click:
        payload["click"] = click
    if attach:
        payload["attach"] = attach

    headers = {"Content-Type": "application/json"}
    if s["token"]:
        headers["Authorization"] = f"Bearer {s['token']}"
    request = urllib.request.Request(s["server"] + "/", data=json.dumps(payload).encode("utf-8"),
                                     headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=15):
            pass
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:200]
        raise NotifyError(f"ntfy refused the message ({exc.code}): {detail}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise NotifyError(f"Can't reach {s['server']}: {getattr(exc, 'reason', exc)}") from exc


def test() -> None:
    send("Rental finder", "If you can read this, notifications are working.", tags=("white_check_mark",))


# ---------------------------------------------------------- messages

def _clean(parts) -> list[str]:
    return [str(p) for p in parts if p not in (None, "")]


def listing_message(listing: dict, watch_name: str) -> dict:
    """The arguments for send() describing one listing."""
    rent = listing.get("rentPerWeek")
    rent_text = (f"${rent:,.0f}/wk" if isinstance(rent, (int, float)) and rent > 0
                 else listing.get("priceDisplay") or "No price")

    beds = listing.get("bedrooms")
    baths = listing.get("bathrooms")
    beds_text = None if beds is None else ("Studio" if beds == 0 else f"{beds} bed")
    baths_text = None if baths is None else f"{baths} bath"
    agency = listing.get("agency")
    lister = agency.get("name") if isinstance(agency, dict) and agency.get("name") else "Private lister"
    available = listing.get("availableFrom")
    parking = listing.get("parking")

    lines = _clean([
        listing.get("address") or listing.get("title"),
        ", ".join(_clean([listing.get("suburb"), listing.get("district")])),
        " · ".join(_clean([beds_text, baths_text, f"Parking: {parking}" if parking else None])),
        f"Available {available}" if available else None,
        f"Listed by {lister}",
        f"Watch: {watch_name}",
    ])

    path = listing.get("canonicalPath")
    picture = listing.get("pictureHref")
    return {
        "title": " · ".join(_clean([rent_text, beds_text, listing.get("propertyType"), listing.get("suburb")])),
        "message": "\n".join(lines),
        "click": "https://www.trademe.co.nz/a/" + path.lstrip("/") if path else None,
        "attach": picture if isinstance(picture, str) and picture.startswith("http") else None,
    }


def summary_message(count: int, watch_name: str) -> dict:
    return {
        "title": f"{count} more new listing{'s' if count != 1 else ''}",
        "message": f"{watch_name}: that's more than fit in one go. Open the app to see them.",
    }
