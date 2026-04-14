#!/usr/bin/env python3
"""
list.am Car Listing Monitor

Periodically scrapes list.am with pre-applied filters and alerts about new listings.

Filters enforced:
  - Price: <= $15,001 (or ~5,850,390 AMD)
  - Engine: Gasoline, Hybrid, Factory LPG/CNG, >1.2L
  - Condition: Not damaged
  - Steering wheel: Left
  - Freshness: renewed after Jul 2025, or posted after Nov 2024

Usage:
    python3 car_monitor.py                  # single scan
    python3 car_monitor.py --watch          # continuous monitoring (every 10 min)
    python3 car_monitor.py --watch --interval 300   # every 5 minutes
    python3 car_monitor.py --reset          # clear seen-listings history
"""

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

SITE = "https://www.list.am"

FILTER_PARAMS = (
    "n=0&bid=55%2C76%2C11%2C13%2C14%2C22%2C27%2C31%2C38%2C53%2C60%2C64%2C75%2C79"
    "&crc=1&price2=15001"
    "&_a27=0"           # not damaged
    "&_a15=1%2C4%2C6"  # gasoline, hybrid, factory LPG/CNG
    "&_a28_1=13"        # engine size > 1.2L
    "&_a1_2=130000"     # max mileage 130k
    "&_a109=1"          # customs cleared
    "&_a16=1"           # left steering wheel
    "&_a22=0"
    "&_a102=0"
)

CATEGORY = "/category/23"

MAX_PRICE_USD = 15_001
MAX_PRICE_AMD = 5_850_390
AMD_PER_USD = 390

ALLOWED_FUELS = {"Benzin", "Hybrid", "Factory LPG/CNG"}

RENEWED_CUTOFF = datetime(2025, 7, 1)
POSTED_CUTOFF = datetime(2024, 11, 1)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "hy,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml",
}

REQUEST_DELAY = 0.6
MAX_RETRIES = 3
SEEN_FILE = Path(__file__).parent / "seen_cars.json"
HTML_FILE = Path(__file__).parent / "cars.html"

ARMENIAN_FUEL = {
    "\u0532\u0565\u0576\u0566\u056b\u0576": "Benzin",
    "\u0540\u056b\u0562\u0580\u056b\u0564": "Hybrid",
    "\u0534\u056b\u0566\u0565\u056c": "Diesel",
    "\u0537\u056c\u0565\u056f\u057f\u0580\u0561\u056f\u0561\u0576": "Electric",
    "\u0533\u0578\u0580\u056e\u0561\u0580\u0561\u0576\u0561\u056f\u0561\u0576 \u0563\u0561\u0566": "Factory LPG/CNG",
}

TAG_NOISE = [
    "VIN-\u0568 \u0576\u0577\u057e\u0561\u056e \u0567",
    "\u0534\u056b\u056c\u0565\u0580",
    "\u0547\u057f\u0561\u057a",
    "\u053c\u056b\u056f\u057e\u056b\u0564\u0561\u0581\u056b\u0578\u0576 \u057e\u0561\u0573\u0561\u057c\u0584",
    "\u0536\u0561\u0576\u0563\u0561\u0570\u0561\u0580\u0565\u0584 \u0570\u056b\u0574\u0561",
    "\u0533\u056b\u0576\u0568 \u057d\u0561\u056f\u0561\u0580\u056f\u0565\u056c\u056b",
]


def build_page_url(page: int) -> str:
    if page <= 1:
        return f"{SITE}{CATEGORY}?{FILTER_PARAMS}"
    return f"{SITE}{CATEGORY}/{page}?{FILTER_PARAMS}"


def fetch(url: str) -> str | None:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            r.raise_for_status()
            return r.text
        except requests.RequestException as e:
            if attempt == MAX_RETRIES:
                print(f"    [!] Failed after {MAX_RETRIES} attempts: {e}", file=sys.stderr)
                return None
            time.sleep(1.5 ** attempt)
    return None


def detect_fuel(text: str) -> str:
    for arm, eng in ARMENIAN_FUEL.items():
        if arm in text:
            return eng
    return "Unknown"


def parse_price(text: str) -> tuple[int | None, str | None]:
    m = re.search(r"\$([\d,]+)", text)
    if m:
        return int(m.group(1).replace(",", "")), "USD"
    m = re.search(r"([\d,]+)\s*\u058f", text)
    if m:
        return int(m.group(1).replace(",", "").replace(" ", "")), "AMD"
    return None, None


def parse_mileage(text: str) -> tuple[int | None, str | None]:
    m = re.search(r"([\d,]+)\s*\u056f\u0574", text)
    if m:
        return int(m.group(1).replace(",", "")), "km"
    m = re.search(r"([\d,]+)\s*\u0574\u0572\u0578\u0576", text)
    if m:
        return int(m.group(1).replace(",", "")), "miles"
    return None, None


def parse_year(text: str) -> int | None:
    m = re.search(r"(\d{4})\s*\u0569\.", text)
    return int(m.group(1)) if m else None


def extract_title(text: str, currency: str) -> str:
    title = text
    if currency == "USD":
        title = re.sub(r"^\$[\d,]+\s*", "", title)
    elif currency == "AMD":
        title = re.sub(r"^[\d,]+\s*\u058f\s*", "", title)
    changed = True
    while changed:
        changed = False
        for tag in TAG_NOISE:
            if title.startswith(tag):
                title = title[len(tag):].lstrip()
                changed = True
    m = re.match(r"(.+?),?\s*\d{4}\s*\u0569\.", title)
    if m:
        title = m.group(1).rstrip(", ")
    return title.strip()


def extract_item_id(href: str) -> str | None:
    m = re.search(r"/item/(\d+)", href)
    return m.group(1) if m else None


def price_usd(price: int, currency: str) -> float:
    if currency == "AMD":
        return price / AMD_PER_USD
    return float(price)


def price_ok(price: int | None, currency: str | None) -> bool:
    if price is None or currency is None:
        return False
    if currency == "USD":
        return price <= MAX_PRICE_USD
    if currency == "AMD":
        return price <= MAX_PRICE_AMD
    return False


def fuel_ok(fuel: str) -> bool:
    return fuel in ALLOWED_FUELS


def fetch_listing_dates(url: str) -> tuple[datetime | None, datetime | None]:
    """Fetch a listing detail page and extract (posted, renewed) dates."""
    en_url = url.replace("/item/", "/en/item/")
    html = fetch(en_url)
    if not html:
        return None, None
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    posted = renewed = None
    m = re.search(r"Posted\s+(\d{2}\.\d{2}\.\d{4})", text)
    if m:
        posted = datetime.strptime(m.group(1), "%d.%m.%Y")
    m = re.search(r"Renewed\s+(\d{2}\.\d{2}\.\d{4})", text)
    if m:
        renewed = datetime.strptime(m.group(1), "%d.%m.%Y")
    return posted, renewed


def is_listing_fresh(posted: datetime | None, renewed: datetime | None) -> bool:
    """Return True if the listing is recent enough to keep."""
    if renewed is not None:
        return renewed >= RENEWED_CUTOFF
    if posted is not None:
        return posted >= POSTED_CUTOFF
    return True


def scrape_listing_page(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    link_tags = soup.find_all("a", href=re.compile(r"^/item/\d+"))

    listings = []
    seen_ids = set()

    for tag in link_tags:
        href = tag.get("href", "")
        item_id = extract_item_id(href)
        if not item_id or item_id in seen_ids:
            continue
        seen_ids.add(item_id)

        full_text = tag.get_text(" ", strip=True)
        if not full_text or len(full_text) < 10:
            continue

        price, currency = parse_price(full_text)
        if price is None:
            continue

        mileage, mileage_unit = parse_mileage(full_text)
        year = parse_year(full_text)
        fuel = detect_fuel(full_text)
        title = extract_title(full_text, currency)
        link = urljoin(SITE, href.split("?")[0])

        listings.append({
            "id": item_id,
            "title": title,
            "price": price,
            "currency": currency,
            "price_usd": round(price_usd(price, currency)),
            "mileage": mileage,
            "mileage_unit": mileage_unit,
            "year": year,
            "fuel": fuel,
            "link": link,
        })

    return listings


def has_next_page(html: str) -> bool:
    soup = BeautifulSoup(html, "html.parser")
    next_text = "\u0540\u0561\u057b\u0578\u0580\u0564\u0568"
    return bool(soup.find("a", string=re.compile(re.escape(next_text))))


def scrape_all_pages() -> list[dict]:
    all_listings = []
    seen_ids = set()
    page = 1

    while True:
        url = build_page_url(page)
        sys.stdout.write(f"\r  Scanning page {page}...")
        sys.stdout.flush()

        html = fetch(url)
        if html is None:
            break

        listings = scrape_listing_page(html)
        if not listings:
            break

        for item in listings:
            if item["id"] not in seen_ids:
                seen_ids.add(item["id"])
                all_listings.append(item)

        if not has_next_page(html):
            break

        page += 1
        time.sleep(REQUEST_DELAY)

    print(f"\r  Scanned {page} page(s) — {len(all_listings)} listings found.       ")
    return all_listings


def load_seen() -> dict:
    if SEEN_FILE.exists():
        try:
            return json.loads(SEEN_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_seen(seen: dict) -> None:
    SEEN_FILE.write_text(json.dumps(seen, ensure_ascii=False, indent=2), encoding="utf-8")


def notify_macos(title: str, message: str, sound: str = "Glass") -> None:
    script = (
        f'display notification "{message}" '
        f'with title "{title}" sound name "{sound}"'
    )
    try:
        subprocess.run(["osascript", "-e", script], check=False, capture_output=True)
    except FileNotFoundError:
        pass


def fmt_price(car: dict) -> str:
    if car["currency"] == "USD":
        return f"${car['price']:,}"
    return f"{car['price']:,} \u058f"


def fmt_mileage(car: dict) -> str:
    if car["mileage"] is None:
        return "N/A"
    unit = "km" if car["mileage_unit"] == "km" else "mi"
    return f"{car['mileage']:,} {unit}"


def print_car(car: dict, prefix: str = "") -> None:
    print(f"{prefix}{car['title']}")
    print(f"{prefix}  Price: {fmt_price(car)}  |  Year: {car.get('year', '?')}  "
          f"|  Mileage: {fmt_mileage(car)}  |  Fuel: {car['fuel']}")
    print(f"{prefix}  {car['link']}")


def parse_make_model(title: str) -> tuple[str, str]:
    """Extract make and model from a listing title like 'Toyota Camry, 2.5 լ'."""
    name_part = title.split(",")[0].strip()
    parts = name_part.split()
    if not parts:
        return ("Unknown", "")
    make = parts[0]
    model = " ".join(parts[1:]) if len(parts) > 1 else ""
    return make, model


def parse_engine_size(title: str) -> float | None:
    """Extract engine size in liters from title like 'Toyota Camry, 2.5 լ'."""
    m = re.search(r"(\d+(?:\.\d+)?)\s*\u056c", title)
    return float(m.group(1)) if m else None


def generate_html(matching: list[dict], new_car_ids: set[str]) -> None:
    """Write an interactive HTML dashboard of scan results."""
    for car in matching:
        car["is_new"] = car["id"] in new_car_ids
        make, model = parse_make_model(car["title"])
        car["make"] = make
        car["model"] = model
        car["engine"] = parse_engine_size(car["title"])
    cars_json = json.dumps(matching, ensure_ascii=False)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Car Monitor — {len(matching)} listings</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 20px 24px;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    background: #0f1117; color: #e1e4e8;
  }}
  h1 {{ margin: 0 0 4px; font-size: 22px; font-weight: 700; color: #f0f3f6; }}
  .subtitle {{ color: #8b949e; font-size: 13px; margin-bottom: 18px; }}
  .stats {{
    display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 18px;
  }}
  .stat {{
    background: #161b22; border: 1px solid #30363d; border-radius: 8px;
    padding: 10px 16px; min-width: 120px;
  }}
  .stat-val {{ font-size: 22px; font-weight: 700; color: #58a6ff; }}
  .stat-label {{ font-size: 11px; color: #8b949e; text-transform: uppercase; letter-spacing: .5px; margin-top: 2px; }}
  .filters {{
    background: #161b22; border: 1px solid #30363d; border-radius: 8px;
    padding: 14px 16px; margin-bottom: 18px;
    display: flex; flex-wrap: wrap; gap: 12px; align-items: flex-end;
  }}
  .filter-group {{ display: flex; flex-direction: column; gap: 3px; }}
  .filter-group label {{ font-size: 11px; color: #8b949e; text-transform: uppercase; letter-spacing: .4px; }}
  .filter-group input, .filter-group select {{
    background: #0d1117; border: 1px solid #30363d; border-radius: 5px;
    color: #e1e4e8; padding: 6px 10px; font-size: 13px; min-width: 90px;
  }}
  .filter-group input:focus, .filter-group select:focus {{
    outline: none; border-color: #58a6ff;
  }}
  .filter-group input[type="text"] {{ min-width: 180px; }}
  .fuel-checks {{ display: flex; gap: 10px; align-items: center; padding-top: 2px; flex-wrap: wrap; }}
  .fuel-checks label {{
    font-size: 13px; color: #c9d1d9; cursor: pointer;
    display: flex; align-items: center; gap: 4px;
    text-transform: none; letter-spacing: 0;
  }}
  .fuel-checks input {{ accent-color: #58a6ff; }}
  .filter-group select[multiple] {{
    min-height: 60px; min-width: 140px;
  }}
  .filter-group select option:checked {{
    background: #1f6feb; color: #fff;
  }}
  .count-bar {{
    font-size: 13px; color: #8b949e; margin-bottom: 8px;
  }}
  .count-bar span {{ color: #e1e4e8; font-weight: 600; }}
  table {{
    width: 100%; border-collapse: collapse; font-size: 13px;
  }}
  thead {{ position: sticky; top: 0; z-index: 2; }}
  th {{
    background: #161b22; border-bottom: 2px solid #30363d;
    padding: 9px 10px; text-align: left; font-weight: 600;
    color: #8b949e; cursor: pointer; user-select: none; white-space: nowrap;
  }}
  th:hover {{ color: #e1e4e8; }}
  th .arrow {{ font-size: 10px; margin-left: 4px; }}
  td {{
    padding: 8px 10px; border-bottom: 1px solid #21262d;
    vertical-align: middle;
  }}
  tr:hover td {{ background: #1c2128; }}
  tr.new-row td {{ background: #122117; }}
  tr.new-row:hover td {{ background: #1a3024; }}
  tr.row-liked td {{ background: #1a2332; }}
  tr.row-liked:hover td {{ background: #222c3d; }}
  tr.row-disliked td {{ background: #2a1c1c; }}
  tr.row-disliked:hover td {{ background: #321f1f; }}
  th.th-actions {{
    cursor: default; user-select: none;
  }}
  th.th-actions:hover {{ color: #8b949e; }}
  .listing-actions {{
    display: flex; gap: 6px; align-items: center; white-space: nowrap;
  }}
  .btn-like, .btn-dislike {{
    display: inline-flex; align-items: center; justify-content: center;
    width: 28px; height: 28px; padding: 0;
    background: #21262d; border: 1px solid #30363d; border-radius: 6px;
    cursor: pointer; font-size: 14px; line-height: 1;
    vertical-align: middle;
  }}
  .btn-like {{ color: #c49aab; }}
  .btn-like:hover {{ color: #f472b6; border-color: #db2777; }}
  .btn-like.is-on {{ color: #f472b6; border-color: #ec4899; background: rgba(236,72,153,0.14); }}
  .btn-dislike {{ color: #d4a72c; }}
  .btn-dislike:hover {{ color: #f0d060; border-color: #b8a030; }}
  .btn-dislike.is-on {{ color: #f0d060; border-color: #ca8a04; background: rgba(202,138,4,0.18); }}
  .badge {{
    display: inline-block; background: #238636; color: #fff;
    font-size: 10px; font-weight: 700; padding: 2px 6px;
    border-radius: 10px; margin-left: 6px; vertical-align: middle;
    letter-spacing: .3px;
  }}
  a {{ color: #58a6ff; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  .price {{ font-weight: 600; white-space: nowrap; }}
  .mileage {{ white-space: nowrap; }}
  .no-results {{
    text-align: center; padding: 40px; color: #484f58; font-size: 15px;
  }}
  .table-wrap {{
    overflow-x: auto; -webkit-overflow-scrolling: touch;
  }}
  @media (max-width: 700px) {{
    body {{ padding: 10px 8px; }}
    h1 {{ font-size: 18px; }}
    .subtitle {{ font-size: 12px; }}
    .stats {{ gap: 8px; }}
    .stat {{ padding: 8px 10px; min-width: 80px; }}
    .stat-val {{ font-size: 17px; }}
    .filters {{
      flex-direction: column; gap: 10px; padding: 12px;
    }}
    .filter-group {{ width: 100%; }}
    .filter-group input, .filter-group select {{
      width: 100%; min-width: 0;
    }}
    .filter-group input[type="text"] {{ min-width: 0; }}
    .filter-group select[multiple] {{ min-height: 50px; }}
    .fuel-checks {{ flex-wrap: wrap; gap: 8px; }}
    table {{ font-size: 12px; min-width: 680px; }}
    th, td {{ padding: 6px 5px; }}
  }}
</style>
</head>
<body>

<h1>list.am Car Monitor</h1>
<div class="subtitle">Generated {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} &mdash; {len(matching)} listings</div>

<div class="stats" id="stats"></div>

<div class="filters">
  <div class="filter-group">
    <label>Search</label>
    <input type="text" id="fSearch" placeholder="e.g. Toyota Camry">
  </div>
  <div class="filter-group">
    <label>Make</label>
    <select multiple id="fMake"></select>
  </div>
  <div class="filter-group">
    <label>Model</label>
    <select multiple id="fModel"></select>
  </div>
  <div class="filter-group">
    <label>Price min ($)</label>
    <input type="number" id="fPriceMin" placeholder="0">
  </div>
  <div class="filter-group">
    <label>Price max ($)</label>
    <input type="number" id="fPriceMax" placeholder="15001">
  </div>
  <div class="filter-group">
    <label>Year min</label>
    <input type="number" id="fYearMin" placeholder="2000">
  </div>
  <div class="filter-group">
    <label>Year max</label>
    <input type="number" id="fYearMax" placeholder="2026">
  </div>
  <div class="filter-group">
    <label>Mileage min (km)</label>
    <input type="number" id="fMileageMin" placeholder="0">
  </div>
  <div class="filter-group">
    <label>Mileage max (km)</label>
    <input type="number" id="fMileageMax" placeholder="200000">
  </div>
  <div class="filter-group">
    <label>Engine min (L)</label>
    <input type="number" id="fEngineMin" placeholder="1.2" step="0.1">
  </div>
  <div class="filter-group">
    <label>Engine max (L)</label>
    <input type="number" id="fEngineMax" placeholder="5.0" step="0.1">
  </div>
  <div class="filter-group">
    <label>Fuel type</label>
    <div class="fuel-checks" id="fuelChecks"></div>
  </div>
  <div class="filter-group">
    <label>&nbsp;</label>
    <label class="fuel-checks" style="padding-top:0">
      <input type="checkbox" id="fNewOnly"> New only
    </label>
  </div>
  <div class="filter-group">
    <label>&nbsp;</label>
    <label class="fuel-checks" style="padding-top:0">
      <input type="checkbox" id="fLikedOnly"> Liked only
    </label>
  </div>
  <div class="filter-group">
    <label>&nbsp;</label>
    <label class="fuel-checks" style="padding-top:0">
      <input type="checkbox" id="fHideDisliked"> Hide disliked (✕)
    </label>
  </div>
  <div class="filter-group">
    <label>&nbsp;</label>
    <label class="fuel-checks" style="padding-top:0">
      <input type="checkbox" id="fNewFirst" checked> New first
    </label>
  </div>
</div>

<div class="count-bar">Showing <span id="visCount">0</span> of <span id="totalCount">0</span> listings</div>

<div class="table-wrap">
<table>
  <thead>
    <tr>
      <th data-col="make">Make <span class="arrow"></span></th>
      <th data-col="model">Model <span class="arrow"></span></th>
      <th class="th-actions">Mark</th>
      <th data-col="price_usd">Price ($) <span class="arrow"></span></th>
      <th data-col="year">Year <span class="arrow"></span></th>
      <th data-col="mileage">Mileage <span class="arrow"></span></th>
      <th data-col="engine">Engine (L) <span class="arrow"></span></th>
      <th data-col="fuel">Fuel <span class="arrow"></span></th>
      <th>Link</th>
    </tr>
  </thead>
  <tbody id="tbody"></tbody>
</table>
</div>
<div class="no-results" id="noResults" style="display:none">No listings match your filters.</div>

<script>
const DATA = {cars_json};

let sortCol = "price_usd", sortAsc = true;

const LS_LIKED = "carMonitorLiked";
const LS_DISLIKED = "carMonitorDisliked";

function loadIdSet(key) {{
  try {{
    const raw = localStorage.getItem(key);
    if (!raw) return new Set();
    const arr = JSON.parse(raw);
    return new Set(Array.isArray(arr) ? arr.map(String) : []);
  }} catch {{
    return new Set();
  }}
}}

function saveIdSet(key, set) {{
  localStorage.setItem(key, JSON.stringify([...set]));
}}

let likedIds = loadIdSet(LS_LIKED);
let dislikedIds = loadIdSet(LS_DISLIKED);

function toggleLike(id) {{
  const sid = String(id);
  if (likedIds.has(sid)) likedIds.delete(sid);
  else {{
    likedIds.add(sid);
    dislikedIds.delete(sid);
  }}
  saveIdSet(LS_LIKED, likedIds);
  saveIdSet(LS_DISLIKED, dislikedIds);
  render();
}}

function toggleDislike(id) {{
  const sid = String(id);
  if (dislikedIds.has(sid)) dislikedIds.delete(sid);
  else {{
    dislikedIds.add(sid);
    likedIds.delete(sid);
  }}
  saveIdSet(LS_LIKED, likedIds);
  saveIdSet(LS_DISLIKED, dislikedIds);
  render();
}}

function initFuelChecks() {{
  const fuels = [...new Set(DATA.map(c => c.fuel))].sort();
  const box = document.getElementById("fuelChecks");
  fuels.forEach(f => {{
    const lbl = document.createElement("label");
    const cb = document.createElement("input");
    cb.type = "checkbox"; cb.checked = true; cb.value = f;
    cb.addEventListener("change", render);
    lbl.appendChild(cb);
    lbl.appendChild(document.createTextNode(" " + f));
    box.appendChild(lbl);
  }});
}}

function initMakeModel() {{
  const makes = [...new Set(DATA.map(c => c.make))].sort();
  const makeEl = document.getElementById("fMake");
  makes.forEach(m => {{
    const opt = document.createElement("option");
    opt.value = m; opt.textContent = m;
    makeEl.appendChild(opt);
  }});
  makeEl.addEventListener("change", () => {{
    updateModelOptions();
    render();
  }});

  const modelEl = document.getElementById("fModel");
  modelEl.addEventListener("change", render);
  updateModelOptions();
}}

function updateModelOptions() {{
  const selMakes = getSelectedValues("fMake");
  const relevantCars = selMakes.size ? DATA.filter(c => selMakes.has(c.make)) : DATA;
  const models = [...new Set(relevantCars.map(c => c.model).filter(Boolean))].sort();
  const modelEl = document.getElementById("fModel");
  const prev = getSelectedValues("fModel");
  modelEl.innerHTML = "";
  models.forEach(m => {{
    const opt = document.createElement("option");
    opt.value = m; opt.textContent = m;
    if (prev.has(m)) opt.selected = true;
    modelEl.appendChild(opt);
  }});
}}

function getSelectedValues(id) {{
  return new Set([...document.getElementById(id).selectedOptions].map(o => o.value));
}}

function getFilters() {{
  const checkedFuels = new Set(
    [...document.querySelectorAll("#fuelChecks input:checked")].map(c => c.value)
  );
  return {{
    search: document.getElementById("fSearch").value.toLowerCase(),
    makes: getSelectedValues("fMake"),
    models: getSelectedValues("fModel"),
    priceMin: Number(document.getElementById("fPriceMin").value) || 0,
    priceMax: Number(document.getElementById("fPriceMax").value) || Infinity,
    yearMin: Number(document.getElementById("fYearMin").value) || 0,
    yearMax: Number(document.getElementById("fYearMax").value) || Infinity,
    mileageMin: Number(document.getElementById("fMileageMin").value) || 0,
    mileageMax: Number(document.getElementById("fMileageMax").value) || Infinity,
    engineMin: Number(document.getElementById("fEngineMin").value) || 0,
    engineMax: Number(document.getElementById("fEngineMax").value) || Infinity,
    fuels: checkedFuels,
    newOnly: document.getElementById("fNewOnly").checked,
    likedOnly: document.getElementById("fLikedOnly").checked,
    hideDisliked: document.getElementById("fHideDisliked").checked,
  }};
}}

function applyFilters(cars) {{
  const f = getFilters();
  return cars.filter(c => {{
    if (f.search && !c.title.toLowerCase().includes(f.search)) return false;
    if (f.makes.size && !f.makes.has(c.make)) return false;
    if (f.models.size && !f.models.has(c.model)) return false;
    if (c.price_usd < f.priceMin || c.price_usd > f.priceMax) return false;
    if (c.year && (c.year < f.yearMin || c.year > f.yearMax)) return false;
    if (c.mileage != null && (c.mileage < f.mileageMin || c.mileage > f.mileageMax)) return false;
    if (c.engine != null && (c.engine < f.engineMin || c.engine > f.engineMax)) return false;
    if (!f.fuels.has(c.fuel)) return false;
    if (f.newOnly && !c.is_new) return false;
    if (f.likedOnly && !likedIds.has(String(c.id))) return false;
    if (f.hideDisliked && dislikedIds.has(String(c.id))) return false;
    return true;
  }});
}}

function sortCars(cars) {{
  const newFirst = document.getElementById("fNewFirst").checked;
  return [...cars].sort((a, b) => {{
    if (newFirst && a.is_new !== b.is_new) return a.is_new ? -1 : 1;
    let va = a[sortCol], vb = b[sortCol];
    if (va == null) va = sortAsc ? Infinity : -Infinity;
    if (vb == null) vb = sortAsc ? Infinity : -Infinity;
    if (typeof va === "string") {{
      va = va.toLowerCase(); vb = (vb || "").toLowerCase();
      return sortAsc ? va.localeCompare(vb) : vb.localeCompare(va);
    }}
    return sortAsc ? va - vb : vb - va;
  }});
}}

function fmtPrice(c) {{
  if (c.currency === "USD") return "$" + c.price.toLocaleString();
  return c.price.toLocaleString() + " \u058F";
}}

function fmtMileage(c) {{
  if (c.mileage == null) return "N/A";
  const unit = c.mileage_unit === "km" ? "km" : "mi";
  return c.mileage.toLocaleString() + " " + unit;
}}

function render() {{
  const filtered = sortCars(applyFilters(DATA));
  const tbody = document.getElementById("tbody");
  const rows = filtered.map(c => {{
    const badge = c.is_new ? '<span class="badge">NEW</span>' : "";
    const sid = String(c.id);
    const liked = likedIds.has(sid);
    const disliked = dislikedIds.has(sid);
    const rowClasses = [];
    if (c.is_new) rowClasses.push("new-row");
    if (liked) rowClasses.push("row-liked");
    if (disliked) rowClasses.push("row-disliked");
    const cls = rowClasses.length ? ` class="${{rowClasses.join(" ")}}"` : "";
    const idAttr = sid.replace(/&/g, "&amp;").replace(/"/g, "&quot;");
    return `<tr${{cls}}>
      <td>${{c.make}}${{badge}}</td>
      <td>${{c.model || c.title}}</td>
      <td class="listing-actions">
        <button type="button" class="btn-like${{liked ? " is-on" : ""}}" data-action="like" data-id="${{idAttr}}" title="Save to revisit" aria-label="Like listing" aria-pressed="${{liked}}">♥</button>
        <button type="button" class="btn-dislike${{disliked ? " is-on" : ""}}" data-action="dislike" data-id="${{idAttr}}" title="Not interested" aria-label="Dislike listing" aria-pressed="${{disliked}}">✕</button>
      </td>
      <td class="price">${{fmtPrice(c)}}</td>
      <td>${{c.year || "?"}}</td>
      <td class="mileage">${{fmtMileage(c)}}</td>
      <td>${{c.engine != null ? c.engine + "L" : "?"}}</td>
      <td>${{c.fuel}}</td>
      <td><a href="${{c.link}}" target="_blank">View</a></td>
    </tr>`;
  }}).join("");
  tbody.innerHTML = rows;
  document.getElementById("visCount").textContent = filtered.length;
  document.getElementById("totalCount").textContent = DATA.length;
  document.getElementById("noResults").style.display = filtered.length ? "none" : "block";
  updateStats(filtered);
}}

function updateStats(filtered) {{
  const prices = filtered.map(c => c.price_usd).filter(p => p != null);
  const newCount = filtered.filter(c => c.is_new).length;
  const minP = prices.length ? Math.min(...prices) : 0;
  const maxP = prices.length ? Math.max(...prices) : 0;
  const years = filtered.map(c => c.year).filter(y => y != null);
  const minY = years.length ? Math.min(...years) : "?";
  const maxY = years.length ? Math.max(...years) : "?";
  document.getElementById("stats").innerHTML = `
    <div class="stat"><div class="stat-val">${{filtered.length}}</div><div class="stat-label">Shown</div></div>
    <div class="stat"><div class="stat-val">${{newCount}}</div><div class="stat-label">New</div></div>
    <div class="stat"><div class="stat-val">$${{minP.toLocaleString()}} &ndash; $${{maxP.toLocaleString()}}</div><div class="stat-label">Price range</div></div>
    <div class="stat"><div class="stat-val">${{minY}} &ndash; ${{maxY}}</div><div class="stat-label">Year range</div></div>
  `;
}}

document.querySelectorAll("th[data-col]").forEach(th => {{
  th.addEventListener("click", () => {{
    const col = th.dataset.col;
    if (sortCol === col) sortAsc = !sortAsc;
    else {{ sortCol = col; sortAsc = true; }}
    document.querySelectorAll("th .arrow").forEach(a => a.textContent = "");
    th.querySelector(".arrow").textContent = sortAsc ? " \\u25B2" : " \\u25BC";
    render();
  }});
}});

["fSearch","fPriceMin","fPriceMax","fYearMin","fYearMax","fMileageMin","fMileageMax","fEngineMin","fEngineMax"].forEach(id => {{
  document.getElementById(id).addEventListener("input", render);
}});
document.getElementById("fNewOnly").addEventListener("change", render);
document.getElementById("fLikedOnly").addEventListener("change", render);
document.getElementById("fHideDisliked").addEventListener("change", render);
document.getElementById("fNewFirst").addEventListener("change", render);

initFuelChecks();
initMakeModel();

document.getElementById("tbody").addEventListener("click", (e) => {{
  const btn = e.target.closest("button[data-action]");
  if (!btn) return;
  const id = btn.getAttribute("data-id");
  if (!id) return;
  if (btn.dataset.action === "like") toggleLike(id);
  else if (btn.dataset.action === "dislike") toggleDislike(id);
}});

render();
document.querySelector('th[data-col="price_usd"] .arrow').textContent = " \\u25B2";
</script>
</body>
</html>"""

    HTML_FILE.write_text(html, encoding="utf-8")


def run_scan() -> list[dict]:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n{'='*70}")
    print(f"  list.am Car Monitor — scan at {now}")
    print(f"  Filters: price <= $15,001 | engine: gas/hybrid/factory LPG | >1.2L")
    print(f"           condition: not damaged | steering: left")
    print(f"           freshness: renewed >= Jul 2025, or posted >= Nov 2024")
    print(f"{'='*70}\n")

    all_cars = scrape_all_pages()

    matching = [
        c for c in all_cars
        if price_ok(c["price"], c["currency"]) and fuel_ok(c["fuel"])
    ]
    matching.sort(key=lambda c: c["price_usd"])

    seen = load_seen()

    need_check = [c for c in matching if "fresh" not in seen.get(c["id"], {})]
    if need_check:
        print(f"\n  Checking listing freshness ({len(need_check)} detail pages)...")
        for i, car in enumerate(need_check, 1):
            sys.stdout.write(f"\r  Checking {i}/{len(need_check)}...")
            sys.stdout.flush()
            posted, renewed = fetch_listing_dates(car["link"])
            fresh = is_listing_fresh(posted, renewed)
            entry = seen.setdefault(car["id"], {})
            entry["fresh"] = fresh
            entry["posted"] = posted.strftime("%Y-%m-%d") if posted else None
            entry["renewed"] = renewed.strftime("%Y-%m-%d") if renewed else None
            time.sleep(REQUEST_DELAY)
        save_seen(seen)
        print(f"\r  Freshness check complete.{' ' * 40}")

    fresh_matching = [c for c in matching if seen.get(c["id"], {}).get("fresh", True)]
    stale_count = len(matching) - len(fresh_matching)
    matching = fresh_matching

    new_cars = [c for c in matching if c["id"] not in seen or
                seen[c["id"]].get("first_seen") == now]

    for c in matching:
        entry = seen.setdefault(c["id"], {})
        entry.update({
            "title": c["title"],
            "price_usd": c["price_usd"],
            "first_seen": entry.get("first_seen", now),
            "last_seen": now,
        })
    save_seen(seen)

    new_car_ids = {c["id"] for c in new_cars}
    generate_html(matching, new_car_ids)
    print(f"\n  Results saved to {HTML_FILE}")

    print(f"\n  Total matching listings: {len(matching)}  ({stale_count} stale filtered out)")
    print(f"  New since last scan:     {len(new_cars)}")

    if new_cars:
        print(f"\n  {'*'*50}")
        print(f"  NEW LISTINGS ({len(new_cars)}):")
        print(f"  {'*'*50}\n")
        for i, car in enumerate(new_cars, 1):
            print_car(car, prefix=f"  {i}. ")
            print()

        if len(new_cars) == 1:
            msg = f"{new_cars[0]['title']} — {fmt_price(new_cars[0])}"
        else:
            msg = f"{len(new_cars)} new cars found (from {fmt_price(new_cars[0])})"
        notify_macos("New Car Listing!", msg)
    else:
        print("\n  No new listings since last scan.\n")

    if matching:
        print(f"\n  {'─'*60}")
        print(f"  ALL MATCHING LISTINGS ({len(matching)}):")
        print(f"  {'─'*60}\n")
        for i, car in enumerate(matching, 1):
            marker = " [NEW]" if car["id"] in {c["id"] for c in new_cars} else ""
            print_car(car, prefix=f"  {i}. ")
            if marker:
                print(f"      {marker}")
            print()

    return new_cars


def main():
    parser = argparse.ArgumentParser(description="Monitor list.am car listings")
    parser.add_argument("--watch", action="store_true",
                        help="Run continuously, re-scanning periodically")
    parser.add_argument("--interval", type=int, default=600,
                        help="Seconds between scans in watch mode (default: 600)")
    parser.add_argument("--reset", action="store_true",
                        help="Clear the seen-listings history and exit")
    args = parser.parse_args()

    if args.reset:
        if SEEN_FILE.exists():
            SEEN_FILE.unlink()
            print("Seen-listings history cleared.")
        else:
            print("No history file found.")
        return

    if not args.watch:
        run_scan()
        return

    print(f"  Watching for new listings every {args.interval}s  (Ctrl+C to stop)\n")
    try:
        while True:
            run_scan()
            next_scan = datetime.now().strftime("%H:%M:%S")
            print(f"  Next scan in {args.interval}s (at ~{next_scan})...")
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n\n  Monitor stopped.")


if __name__ == "__main__":
    main()
