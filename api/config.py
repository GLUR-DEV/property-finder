"""Settings, overridable with environment variables."""

import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).parent / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

SEARCHES_CSV = DATA_DIR / "searches.csv"      # saved suburbs (region, district, suburb_name, suburb_id)
HISTORY_DB = DATA_DIR / "history.db"          # every search, one row per listing per search
LISTINGS_JSON = DATA_DIR / "listings.json"    # latest search: metadata + every listing, all fields
LISTINGS_CSV = DATA_DIR / "listings.csv"      # same listings flattened to columns
MARKETPLACE_JSON = DATA_DIR / "marketplace.json"        # Facebook Marketplace listings from the extension
MARKETPLACE_IMAGES = DATA_DIR / "marketplace_images"     # their photos, saved here because Facebook's links expire
RAW_JSON = DATA_DIR / "response_raw.json"     # first page's embedded JSON, for inspection
DEBUG_HTML = DATA_DIR / "page.html"           # saved when a results page has no listings

START_URL = "https://www.trademe.co.nz/a/property/residential/rent"
ROOT_URL = START_URL

# The scripts ran with a visible browser. Set HEADLESS=1 to try without one.
HEADLESS = os.environ.get("HEADLESS", "0") == "1"
MAX_PAGES = int(os.environ.get("MAX_PAGES", "100"))
PAGE_DELAY = float(os.environ.get("PAGE_DELAY", "1.5"))

# Facebook Marketplace (listings captured by the browser extension)
MARKETPLACE_KEEP_DAYS = int(os.environ.get("MARKETPLACE_KEEP_DAYS", "14"))   # drop a listing this long after it was last seen
MARKETPLACE_MAX_PHOTOS = 6               # photos saved per listing
MARKETPLACE_MAX_PRICE = 5000             # cards above this are skipped (sales, placeholders)
MARKETPLACE_PLACEHOLDER_PRICES = {0, 1, 12, 123, 1234, 12345, 123456, 1234567}   # cards with these prices are skipped
# Sellers often leave the price period on 'per month'. A monthly price whose weekly equivalent
# is below these is treated as a weekly price instead.
MARKETPLACE_MIN_WEEKLY_HOME = 250        # a house, unit or apartment
MARKETPLACE_MIN_WEEKLY_ROOM = 100        # a single room

# Watches (scheduled searches that notify your phone)
MIN_WATCH_MINUTES = 15                 # shortest interval a watch may use
WATCH_TICK_SECONDS = 15                # how often the scheduler looks for watches that are due
MAX_NOTIFICATIONS_PER_RUN = int(os.environ.get("MAX_NOTIFICATIONS_PER_RUN", "5"))
# A watch saves a history run (for the Statistics trends) at most this often
HISTORY_MIN_GAP_HOURS = float(os.environ.get("HISTORY_MIN_GAP_HOURS", "6"))
