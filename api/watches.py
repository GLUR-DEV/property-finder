"""Watches: saved searches that re-run on a schedule and send new listings to your phone.

Each run also saves its results as the latest search, so the Listings and Map pages
show them just as they do for a manual search.

A watch remembers every listing it has already seen (watch_seen). Its first run
only saves a baseline, so you aren't sent the whole market; after that, any
listing it hasn't seen before is a new one. The scheduler is a thread inside the
API, so watches run for as long as the API does."""

import json
import threading
from contextlib import closing
from datetime import datetime, timedelta, timezone
from typing import Callable

import config
import history
import notify
import scraper
import store
from jobs import Busy, Jobs

SCHEMA = """
CREATE TABLE IF NOT EXISTS watches (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    name             TEXT    NOT NULL,
    enabled          INTEGER NOT NULL DEFAULT 1,
    notify           INTEGER NOT NULL DEFAULT 1,
    interval_minutes INTEGER NOT NULL,
    params           TEXT    NOT NULL,   -- the search as the web app sent it (camelCase JSON)
    url              TEXT    NOT NULL,   -- the Trade Me results URL built from it
    use_window       INTEGER NOT NULL DEFAULT 0,  -- 1 once headless mode has been blocked
    baselined        INTEGER NOT NULL DEFAULT 0,  -- 1 once the first run has saved its baseline
    created_at       TEXT    NOT NULL,
    last_run_at      TEXT,
    last_status      TEXT,               -- ok | warning | error
    last_message     TEXT,
    last_count       INTEGER,
    last_new         INTEGER,
    next_run_at      TEXT
);
CREATE TABLE IF NOT EXISTS watch_seen (
    watch_id   INTEGER NOT NULL REFERENCES watches (id) ON DELETE CASCADE,
    listing_id INTEGER NOT NULL,
    PRIMARY KEY (watch_id, listing_id)
);
"""

Progress = scraper.Progress
_running: set[int] = set()
_lock = threading.Lock()


def init() -> None:
    with closing(history.connect()) as conn, conn:
        conn.executescript(SCHEMA)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(when: datetime) -> str:
    return when.astimezone(timezone.utc).isoformat(timespec="seconds")


def _scope_of(url: str) -> str:
    """Same search regardless of suburb order, so reordering doesn't count as a change."""
    scope = history.scope_from_url(url)
    return history.scope_key(scope) if scope else url


def _next_run(last_run_at: str | None, interval_minutes: int) -> str:
    """An interval after the last run, or right away if it has never run."""
    last = history.parse_time(last_run_at)
    return _iso(last + timedelta(minutes=interval_minutes)) if last else _iso(_now())


# ----------------------------------------------------------- reading

def _to_dict(row) -> dict:
    scope = history.scope_from_url(row["url"])
    with _lock:
        running = row["id"] in _running
    return {
        **json.loads(row["params"]),
        "id": row["id"], "name": row["name"],
        "label": history.scope_label(scope) if scope else row["name"],
        "enabled": bool(row["enabled"]), "notify": bool(row["notify"]),
        "intervalMinutes": row["interval_minutes"],
        "useWindow": bool(row["use_window"]), "baselined": bool(row["baselined"]),
        "lastRunAt": row["last_run_at"], "lastStatus": row["last_status"],
        "lastMessage": row["last_message"], "lastCount": row["last_count"],
        "lastNew": row["last_new"], "nextRunAt": row["next_run_at"], "running": running,
    }


def _row(watch_id: int):
    with closing(history.connect()) as conn:
        return conn.execute("SELECT * FROM watches WHERE id = ?", (watch_id,)).fetchone()


def get(watch_id: int) -> dict | None:
    row = _row(watch_id)
    return _to_dict(row) if row else None


def list_all() -> list[dict]:
    with closing(history.connect()) as conn:
        return [_to_dict(r) for r in conn.execute("SELECT * FROM watches ORDER BY id")]


def due() -> list[int]:
    with closing(history.connect()) as conn:
        return [r["id"] for r in conn.execute(
            "SELECT id FROM watches WHERE enabled = 1 AND (next_run_at IS NULL OR next_run_at <= ?)"
            " ORDER BY next_run_at", (_iso(_now()),))]


# ----------------------------------------------------------- editing

def create(name: str, interval: int, params: dict, url: str, enabled: bool, notify_on: bool) -> dict:
    now = _iso(_now())
    with closing(history.connect()) as conn, conn:
        watch_id = conn.execute(
            "INSERT INTO watches (name, enabled, notify, interval_minutes, params, url, created_at, next_run_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (name, int(enabled), int(notify_on), interval, json.dumps(params), url, now, now),
        ).lastrowid
    return get(watch_id)  # type: ignore[return-value]


def update(watch_id: int, name: str, interval: int, params: dict, url: str,
           enabled: bool, notify_on: bool) -> dict | None:
    """Changing the search itself starts the watch over (new baseline, no old listings)."""
    row = _row(watch_id)
    if row is None:
        return None
    with closing(history.connect()) as conn, conn:
        conn.execute(
            "UPDATE watches SET name=?, enabled=?, notify=?, interval_minutes=?, params=?, url=?,"
            " next_run_at=? WHERE id=?",
            (name, int(enabled), int(notify_on), interval, json.dumps(params), url,
             _next_run(row["last_run_at"], interval), watch_id))
        if _scope_of(row["url"]) != _scope_of(url):
            conn.execute("DELETE FROM watch_seen WHERE watch_id = ?", (watch_id,))
            conn.execute(
                "UPDATE watches SET baselined=0, use_window=0, next_run_at=?, last_status=NULL,"
                " last_message=NULL, last_count=NULL, last_new=NULL WHERE id=?",
                (_iso(_now()), watch_id))
    return get(watch_id)


def set_enabled(watch_id: int, enabled: bool) -> dict | None:
    row = _row(watch_id)
    if row is None:
        return None
    with closing(history.connect()) as conn, conn:
        conn.execute("UPDATE watches SET enabled=?, next_run_at=? WHERE id=?",
                     (int(enabled), _next_run(row["last_run_at"], row["interval_minutes"]), watch_id))
    return get(watch_id)


def delete(watch_id: int) -> bool:
    with closing(history.connect()) as conn, conn:
        return conn.execute("DELETE FROM watches WHERE id = ?", (watch_id,)).rowcount > 0


# ----------------------------------------------------------- running

def _finish(watch_id: int, status: str, message: str, count: int | None, new: int | None,
            *, use_window: bool = False, baselined: bool = False) -> None:
    """Record how a run went and schedule the next one an interval from now."""
    now = _now()
    with closing(history.connect()) as conn, conn:
        row = conn.execute("SELECT interval_minutes FROM watches WHERE id = ?", (watch_id,)).fetchone()
        if row is None:  # deleted while it was running
            return
        conn.execute(
            "UPDATE watches SET last_run_at=?, last_status=?, last_message=?, last_count=?, last_new=?,"
            " next_run_at=?, use_window = use_window OR ?, baselined = baselined OR ? WHERE id=?",
            (_iso(now), status, message, count, new,
             _iso(now + timedelta(minutes=row["interval_minutes"])), int(use_window), int(baselined), watch_id))


def _seen(watch_id: int) -> set[int]:
    with closing(history.connect()) as conn:
        return {r["listing_id"] for r in
                conn.execute("SELECT listing_id FROM watch_seen WHERE watch_id = ?", (watch_id,))}


def _mark_seen(watch_id: int, listing_ids: list[int]) -> None:
    with closing(history.connect()) as conn, conn:
        conn.executemany("INSERT OR IGNORE INTO watch_seen (watch_id, listing_id) VALUES (?, ?)",
                         [(watch_id, i) for i in listing_ids])


def _scrape(watch, progress: Progress, expect_results: bool) -> tuple[list[dict], bool]:
    """Returns (listings, headless_was_blocked). Starts headless; if that fails or comes
    back empty when listings were expected, tries again with a visible window."""
    url = watch["url"]
    if watch["use_window"]:
        return scraper.fetch_listings(url, progress, headless=False), False
    try:
        listings = scraper.fetch_listings(url, progress, headless=True)
        if listings or not expect_results:
            return listings, False
    except Exception:
        pass  # the visible attempt below reports its own error if it fails too
    progress(0, 0, "Nothing came back headless, so retrying with a browser window")
    listings = scraper.fetch_listings(url, progress, headless=False)
    return listings, bool(listings)


def _record_history(url: str, listings: list[dict]) -> None:
    """Keep trends, but not a run every 15 minutes: that would bloat the database and
    slow the Statistics page. At most one run per scope every HISTORY_MIN_GAP_HOURS."""
    scope = history.scope_from_url(url)
    if scope:
        with closing(history.connect()) as conn:
            last = conn.execute("SELECT MAX(searched_at) AS t FROM searches WHERE scope_key = ?",
                                (history.scope_key(scope),)).fetchone()["t"]
        when = history.parse_time(last)
        if when and _now() - when < timedelta(hours=config.HISTORY_MIN_GAP_HOURS):
            return
    history.record(url, listings)


def _notify(new: list[dict], watch_name: str) -> tuple[list[int], str | None]:
    """Send the new listings, newest first. Returns the IDs that are dealt with and a
    problem, if any. Listings that failed to send stay unseen, so the next run retries."""
    ids = [l["listingId"] for l in new]
    if not notify.get()["topic"]:
        return ids, "ntfy isn't set up, so nothing was sent."

    ordered = sorted(new, key=lambda l: l.get("startDate") or "", reverse=True)
    cap = config.MAX_NOTIFICATIONS_PER_RUN
    done: list[int] = []
    try:
        for listing in ordered[:cap]:
            notify.send(**notify.listing_message(listing, watch_name))
            done.append(listing["listingId"])
        if len(ordered) > cap:
            notify.send(**notify.summary_message(len(ordered) - cap, watch_name))
            done += [l["listingId"] for l in ordered[cap:]]
    except notify.NotifyError as exc:
        return done, str(exc)
    return done, None


def _run(watch, progress: Progress) -> dict:
    watch_id = watch["id"]
    seen = _seen(watch_id)
    first = not watch["baselined"]

    listings, switched = _scrape(watch, progress, expect_results=first or bool(seen))
    found = {l["listingId"]: l for l in listings if l.get("listingId") is not None}
    note = " Headless mode was blocked, so this watch now opens a window." if switched else ""

    if not found and seen:
        message = "Trade Me returned no listings, so this run was ignored." + note
        _finish(watch_id, "warning", message, 0, 0, use_window=switched)
        return {"count": 0, "new": 0, "message": message}

    # The Listings and Map pages show the latest search, whether it was a watch or a manual one
    store.save(watch["url"], listings)
    if found:
        _record_history(watch["url"], listings)

    new = [l for i, l in found.items() if i not in seen]
    status, problem, done = "ok", None, []
    if first:
        _mark_seen(watch_id, list(found))
        message = (f"Baseline saved: {len(found)} listings. "
                   "New ones from now on are sent to your phone." if watch["notify"]
                   else f"Baseline saved: {len(found)} listings.")
        new = []
    else:
        if new and watch["notify"]:
            done, problem = _notify(new, watch["name"])
        else:
            done = [l["listingId"] for l in new]
        _mark_seen(watch_id, done)
        if problem:
            status, message = "warning", f"{len(new)} new, but {problem}"
        elif not new:
            message = "No new listings."
        else:
            message = f"{len(new)} new listing{'s' if len(new) != 1 else ''}" + (
                " sent to your phone." if watch["notify"] else ".")

    message += note
    _finish(watch_id, status, message, len(found), len(new), use_window=switched, baselined=True)
    return {"count": len(found), "new": len(new), "message": message}


def run(watch_id: int, progress: Progress | None = None) -> dict:
    """Scrape one watch now. Call with the browser lock held (Jobs.exclusive or Jobs.start).
    Returns {"count", "new", "message"}; a failure is recorded on the watch and re-raised."""
    watch = _row(watch_id)
    if watch is None:
        raise KeyError(watch_id)
    with _lock:
        _running.add(watch_id)
    try:
        return _run(watch, progress or (lambda *_: None))
    except Exception as exc:
        _finish(watch_id, "error", str(exc) or exc.__class__.__name__, None, None)
        raise
    finally:
        with _lock:
            _running.discard(watch_id)


# --------------------------------------------------------- scheduler

class Scheduler:
    """Looks for due watches every few seconds and runs them one at a time. A watch
    that comes due while you're running a search yourself waits for the browser."""

    def __init__(self, jobs: Jobs, runner: Callable[[int], dict] = run) -> None:
        self._jobs = jobs
        self._runner = runner
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="watch-scheduler", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(config.WATCH_TICK_SECONDS):
            try:
                self.tick()
            except Exception as exc:  # never let the thread die
                print(f"[watch] scheduler error: {exc}", flush=True)

    def tick(self) -> None:
        for watch_id in due():
            if self._stop.is_set():
                return
            try:
                with self._jobs.exclusive():
                    print(f"[watch] running #{watch_id}", flush=True)
                    result = self._runner(watch_id)
                    print(f"[watch] #{watch_id}: {result['message']}", flush=True)
            except Busy:
                return  # try again on the next tick
            except KeyError:
                continue  # deleted since we looked
            except Exception as exc:
                print(f"[watch] #{watch_id} failed: {exc}", flush=True)
