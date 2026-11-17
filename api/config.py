"""Settings, overridable with environment variables."""

import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).parent / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

SEARCHES_CSV = DATA_DIR / "searches.csv"      # saved suburbs (region, district, suburb_name, suburb_id)
LISTINGS_JSON = DATA_DIR / "listings.json"    # latest search: metadata + every listing, all fields
LISTINGS_CSV = DATA_DIR / "listings.csv"      # same listings flattened to columns
RAW_JSON = DATA_DIR / "response_raw.json"     # first page's embedded JSON, for inspection
DEBUG_HTML = DATA_DIR / "page.html"           # saved when a results page has no listings

START_URL = "https://www.trademe.co.nz/a/property/residential/rent"
ROOT_URL = START_URL

# The scripts ran with a visible browser. Set HEADLESS=1 to try without one.
HEADLESS = os.environ.get("HEADLESS", "0") == "1"
MAX_PAGES = int(os.environ.get("MAX_PAGES", "50"))
PAGE_DELAY = float(os.environ.get("PAGE_DELAY", "1.5"))
