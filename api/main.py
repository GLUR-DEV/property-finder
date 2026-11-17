"""Local API for the rental finder. Run:  uvicorn main:app --port 8000

  GET  /api/locations                  saved regions, districts and suburbs
  GET  /api/site/regions               regions listed on Trade Me (opens a browser)
  GET  /api/site/districts?region=...  districts of a region (opens a browser)
  POST /api/locations/grab             save a district's suburbs   -> job
  POST /api/searches                   scrape listings             -> job
  GET  /api/jobs/{id}                  job progress
  GET  /api/listings                   the latest search's listings
  GET  /api/stats?scope_key=...        chart data from the saved history of a search
  GET/POST /api/watches                scheduled searches that notify your phone
  PUT/DELETE /api/watches/{id}         edit or remove a watch
  PATCH /api/watches/{id}/enabled      pause or resume a watch
  POST /api/watches/{id}/run           run a watch now            -> job
  GET/PUT /api/notifications           ntfy settings (server, topic, token)
  POST /api/notifications/test         send a test notification
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

import config
import history
import locations
import notify
import scraper
import stats
import store
import watches
from jobs import Busy, Job, Jobs

@asynccontextmanager
async def lifespan(_: FastAPI):
    history.init()
    history.import_existing()  # first run: keep the results already in listings.json
    notify.init()
    watches.init()
    scheduler.start()
    yield
    scheduler.stop()


app = FastAPI(title="Rental finder API", lifespan=lifespan)
jobs = Jobs()
scheduler = watches.Scheduler(jobs)

BUSY = HTTPException(409, "Another browser task is still running. Wait for it to finish.")


class Camel(BaseModel):
    """JSON in camelCase to match the listings Trade Me returns."""
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class SearchRequest(Camel):
    region: str
    district: str
    suburb_ids: list[str] = Field(min_length=1)
    price_min: int | None = Field(None, ge=0)
    price_max: int | None = Field(None, ge=0)
    bedrooms_min: int | None = Field(None, ge=0, le=6)
    bedrooms_max: int | None = Field(None, ge=0, le=6)
    bathrooms_min: int | None = Field(None, ge=0, le=6)
    bathrooms_max: int | None = Field(None, ge=0, le=6)
    property_types: list[str] = []

    @model_validator(mode="after")
    def check(self) -> "SearchRequest":
        for label in ("price", "bedrooms", "bathrooms"):
            low, high = getattr(self, f"{label}_min"), getattr(self, f"{label}_max")
            if low is not None and high is not None and low > high:
                raise ValueError(f"Min {label} can't be higher than max {label}")
        bad = [t for t in self.property_types if t not in locations.PROPERTY_TYPES]
        if bad:
            raise ValueError(f"Unknown property types: {', '.join(bad)}")
        return self

    def filters(self) -> list[tuple[str, object]]:
        """In the order the site puts them in the URL; skipped ones are left out."""
        pairs: list[tuple[str, object]] = [
            ("price_min", self.price_min), ("price_max", self.price_max),
            ("bedrooms_min", self.bedrooms_min), ("bedrooms_max", self.bedrooms_max),
            ("bathrooms_min", self.bathrooms_min), ("bathrooms_max", self.bathrooms_max),
        ]
        return [(k, v) for k, v in pairs if v is not None] + [
            ("property_type", t) for t in self.property_types
        ]


class GrabRequest(Camel):
    region: str
    district: str


class WatchRequest(SearchRequest):
    name: str = Field(min_length=1, max_length=60)
    interval_minutes: int = Field(ge=config.MIN_WATCH_MINUTES, le=10_080)
    enabled: bool = True
    notify: bool = True


class EnabledRequest(Camel):
    enabled: bool


class NotifyRequest(Camel):
    server: str = ""
    topic: str = ""
    token: str | None = None   # None keeps the saved token, "" removes it


def search_url(req: SearchRequest) -> str:
    """The Trade Me results URL for a request, after checking it uses saved locations."""
    saved = locations.load_locations().get(req.region, {}).get(req.district)
    if saved is None:
        raise HTTPException(400, f"{req.region} / {req.district} isn't in the saved locations.")
    unknown = set(req.suburb_ids) - {sid for _, sid in saved}
    if unknown:
        raise HTTPException(400, f"Suburb IDs not saved for {req.district}: {', '.join(sorted(unknown))}")
    return locations.build_url(req.region, req.district, req.suburb_ids, req.filters())


def watch_args(req: WatchRequest) -> dict:
    name = req.name.strip()
    if not name:
        raise HTTPException(400, "Give the watch a name.")
    return {
        "name": name, "interval": req.interval_minutes, "url": search_url(req),
        "params": req.model_dump(by_alias=True, exclude={"name", "interval_minutes", "enabled", "notify"}),
        "enabled": req.enabled, "notify_on": req.notify,
    }


# ------------------------------------------------------------ routes

@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/locations")
def get_locations() -> list[dict]:
    return locations.locations_as_json()


@app.get("/api/site/regions")
def site_regions() -> list[str]:
    try:
        with jobs.exclusive():
            return scraper.site_regions()
    except Busy:
        raise BUSY
    except Exception as exc:
        raise HTTPException(502, f"Could not read regions from Trade Me: {exc}")


@app.get("/api/site/districts")
def site_districts(region: str) -> list[str]:
    try:
        with jobs.exclusive():
            return scraper.site_districts(region)
    except Busy:
        raise BUSY
    except Exception as exc:
        raise HTTPException(502, f"Could not read districts from Trade Me: {exc}")


@app.post("/api/locations/grab", status_code=202)
def grab_locations(req: GrabRequest) -> dict:
    def work(job: Job) -> None:
        job.message = f"Reading suburbs for {req.district}"
        found = scraper.grab_suburbs(req.region, req.district)
        added = locations.save_suburbs(req.region, req.district, found)
        job.count = added
        job.message = f"{len(found)} suburbs found, {added} new saved"

    try:
        return jobs.start("locations", work).to_dict()
    except Busy:
        raise BUSY


@app.post("/api/searches", status_code=202)
def start_search(req: SearchRequest) -> dict:
    url = search_url(req)

    def work(job: Job) -> None:
        def progress(page: int, count: int, message: str) -> None:
            job.page, job.count, job.message = page, count, message

        listings = scraper.fetch_listings(url, progress)
        store.save(url, listings)
        history.record(url, listings)
        job.count = len(listings)
        job.message = (f"Saved {len(listings)} listings" if listings
                       else "No listings matched this search")

    try:
        return jobs.start("search", work).to_dict()
    except Busy:
        raise BUSY


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "Unknown job")
    return job.to_dict()


@app.get("/api/listings")
def get_listings() -> dict:
    return store.load()


@app.get("/api/stats")
def get_stats(scope_key: str | None = None) -> dict:
    return stats.build(scope_key)


# ------------------------------------------------------------ watches

@app.get("/api/watches")
def get_watches() -> list[dict]:
    return watches.list_all()


@app.post("/api/watches", status_code=201)
def create_watch(req: WatchRequest) -> dict:
    return watches.create(**watch_args(req))


@app.put("/api/watches/{watch_id}")
def update_watch(watch_id: int, req: WatchRequest) -> dict:
    updated = watches.update(watch_id, **watch_args(req))
    if updated is None:
        raise HTTPException(404, "Unknown watch")
    return updated


@app.patch("/api/watches/{watch_id}/enabled")
def set_watch_enabled(watch_id: int, req: EnabledRequest) -> dict:
    updated = watches.set_enabled(watch_id, req.enabled)
    if updated is None:
        raise HTTPException(404, "Unknown watch")
    return updated


@app.delete("/api/watches/{watch_id}", status_code=204)
def delete_watch(watch_id: int) -> None:
    if not watches.delete(watch_id):
        raise HTTPException(404, "Unknown watch")


@app.post("/api/watches/{watch_id}/run", status_code=202)
def run_watch(watch_id: int) -> dict:
    if watches.get(watch_id) is None:
        raise HTTPException(404, "Unknown watch")

    def work(job: Job) -> None:
        def progress(page: int, count: int, message: str) -> None:
            job.page, job.count, job.message = page, count, message

        result = watches.run(watch_id, progress)
        job.count, job.message = result["count"], result["message"]

    try:
        return jobs.start("watch", work).to_dict()
    except Busy:
        raise BUSY


# ----------------------------------------------------- notifications

@app.get("/api/notifications")
def get_notifications() -> dict:
    return notify.public()


@app.put("/api/notifications")
def save_notifications(req: NotifyRequest) -> dict:
    try:
        return notify.save(req.server, req.topic, req.token)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/notifications/test")
def test_notification() -> dict:
    try:
        notify.test()
    except notify.NotifyError as exc:
        raise HTTPException(502, str(exc))
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000)
