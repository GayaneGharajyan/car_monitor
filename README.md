# list.am Car Listing Monitor

Scrapes [list.am](https://www.list.am) car listings with pre-configured filters and generates an interactive HTML dashboard. Sends macOS notifications when new listings appear.

## Filters

| Filter | Value |
|---|---|
| Price | <= $10,000 (or ~3,900,000 AMD) |
| Makes | Chevrolet, Ford, Honda, Hyundai, Kia, Mazda, Mitsubishi, Nissan, Subaru, Toyota, Peugeot, Renault, Suzuki, Volkswagen |
| Engine type | Gasoline, Hybrid, Factory LPG/CNG |
| Engine size | > 1.2L |
| Mileage | <= 130,000 km |
| Condition | Not damaged |
| Steering wheel | Left |
| Customs | Cleared |
| Freshness | Renewed after Jul 2025, or posted after Nov 2024 |

## Requirements

- Python 3.10+
- `requests`
- `beautifulsoup4`

```bash
pip install requests beautifulsoup4
```

## Usage

```bash
# Single scan — scrapes all pages, filters, and generates cars.html
python3 car_monitor.py

# Continuous monitoring — re-scans every 10 minutes
python3 car_monitor.py --watch

# Custom interval — re-scan every 5 minutes
python3 car_monitor.py --watch --interval 300

# Clear seen-listings history (all listings will appear as "new")
python3 car_monitor.py --reset
```

## Output

### Terminal
Prints a summary of matching and new listings to the console.

### macOS Notification
When new listings are found, a native macOS notification is sent (with sound).

### Interactive HTML Dashboard (`cars.html`)
After each scan, a self-contained `cars.html` file is generated with:

- **Summary stats** — total listings, new count, price range, year range
- **Filters** — search, make, model, price, year, mileage, engine size, fuel type, "new only"
- **Sortable table** — click any column header to sort
- **Mobile-friendly** — responsive layout with horizontally scrollable table

Open it with:
```bash
open cars.html
```

To view on your phone (same Wi-Fi):
```bash
python3 -m http.server 8000
# Then open http://<your-mac-ip>:8000/cars.html on your phone
```

## Files

| File | Description |
|---|---|
| `car_monitor.py` | Main script |
| `cars.html` | Generated dashboard (after first scan) |
| `seen_cars.json` | Tracks previously seen listings (auto-created) |
