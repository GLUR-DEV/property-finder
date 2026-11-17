"""Local API for the rental finder. Run:  uvicorn main:app --port 8000

  GET  /api/locations                  saved regions, districts and suburbs
  GET  /api/site/regions               regions listed on Trade Me (opens a browser)
  GET  /api/site/districts?region=...  districts of a region (opens a browser)
  POST /api/locations/grab             save a district's suburbs   -> job
  POST /api/searches                   scrape listings             -> job
  GET  /api/jobs/{id}                  job progress
  GET  /api/listings                   the latest search's listings
"""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

import locations
import scraper
import store
from jobs import Busy, Job, Jobs

app = FastAPI(title="Rental finder API")
jobs = Jobs()

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
    saved = locations.load_locations().get(req.region, {}).get(req.district)
    if saved is None:
        raise HTTPException(400, f"{req.region} / {req.district} isn't in the saved locations.")
    unknown = set(req.suburb_ids) - {sid for _, sid in saved}
    if unknown:
        raise HTTPException(400, f"Suburb IDs not saved for {req.district}: {', '.join(sorted(unknown))}")

    url = locations.build_url(req.region, req.district, req.suburb_ids, req.filters())

    def work(job: Job) -> None:
        def progress(page: int, count: int, message: str) -> None:
            job.page, job.count, job.message = page, count, message

        listings = scraper.fetch_listings(url, progress)
        store.save(url, listings)
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000)
