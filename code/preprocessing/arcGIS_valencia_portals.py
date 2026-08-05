# download_portals_valencia.py
import requests, json, time

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
GLIMS_DIR = BASE_DIR.parent.parent
DATA_DIR = GLIMS_DIR / "data" / "valencia"

BASE = (
    "https://geoportal.valencia.es/server/rest/services/"
    "OPENDATA/UrbanismoEInfraestructuras/MapServer/217/query"
)
EXPECTED = 56653  # Expected number of features for validation


def fetch_all():
    offset, page, feats = 0, 2000, []
    while True:
        params = {
            "where": "1=1",
            "outFields": "*",
            "outSR": "4326",              # WGS84 (longitude/latitude)
            "f": "geojson",
            "resultOffset": offset,
            "resultRecordCount": page,
            "orderByFields": "objectid",  # Stable ordering to avoid duplicates or skipped records
        }

        for attempt in range(3):          # Retry failed requests up to 3 times
            try:
                r = requests.get(BASE, params=params, timeout=120)
                r.raise_for_status()
                data = r.json()
                break
            except Exception as e:
                print(f"  [!] Failed page offset={offset} (attempt {attempt + 1}): {e}")
                time.sleep(2)
        else:
            raise RuntimeError(f"Could not download page with offset={offset}")

        batch = data.get("features", [])
        if not batch:
            break

        feats.extend(batch)
        print(f"  {len(feats)}/{EXPECTED} portals downloaded...")

        if len(batch) < page:
            break

        offset += page
        time.sleep(0.3)

    return feats


feats = fetch_all()
out = {"type": "FeatureCollection", "features": feats}

DATA_DIR.mkdir(parents=True, exist_ok=True)

output_file = DATA_DIR / "portals_valencia_full.geojson"

with open(output_file, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False)

print(f"File saved to: {output_file}")

print(f"\nTotal downloaded: {len(feats)} (expected {EXPECTED})")
if len(feats) != EXPECTED:
    print("  [!] Downloaded feature count does not match the expected total. Please verify the output.")
else:
    print("  OK: Download completed successfully.")