"""
Test Alert Injector for ESP32 Emergency Alert System

Sends realistic Pikud HaOref-format JSON to the ESP32's /test_inject endpoint.
The device must be in Test Mode (enable via web UI -> Test page).

The script fetches the device's monitored cities via /api/cities and uses
them in test payloads, so alerts go through the exact same city-matching
pipeline as real alerts from oref.org.il.

Usage:
    python test_alerts.py <ESP32_IP>
    python test_alerts.py 192.168.1.100
"""

import sys
import json
import os
import time
import urllib.request

# Bypass proxy for local ESP32 connections
urllib.request.install_opener(
    urllib.request.build_opener(urllib.request.ProxyHandler({}))
)

# Load areas data from the project's areas.json
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AREAS_FILE = os.path.join(SCRIPT_DIR, "..", "arduino", "data", "areas.json")

# ---- Real Pikud HaOref alert templates (exact titles & descriptions) ----

ALERT_TYPES = [
    {
        "cat": 1,
        "title": "\u05d9\u05e8\u05d9 \u05e8\u05e7\u05d8\u05d5\u05ea \u05d5\u05d8\u05d9\u05dc\u05d9\u05dd",
        "desc": "\u05d4\u05d9\u05db\u05e0\u05e1\u05d5 \u05dc\u05de\u05e8\u05d7\u05d1 \u05d4\u05de\u05d5\u05d2\u05df",
        "label": "Missiles (cat 1) \u2192 SHELTER",
    },
    {
        "cat": 3,
        "title": "\u05e8\u05e2\u05d9\u05d3\u05ea \u05d0\u05d3\u05de\u05d4",
        "desc": "\u05e6\u05d0\u05d5 \u05de\u05d4\u05de\u05d1\u05e0\u05d9\u05dd \u05dc\u05e9\u05d8\u05d7 \u05e4\u05ea\u05d5\u05d7",
        "label": "Earthquake (cat 3) \u2192 SHELTER",
    },
    {
        "cat": 6,
        "title": "\u05d7\u05d3\u05d9\u05e8\u05ea \u05db\u05dc\u05d9 \u05d8\u05d9\u05e1 \u05e2\u05d5\u05d9\u05d9\u05df",
        "desc": "\u05d4\u05d9\u05db\u05e0\u05e1\u05d5 \u05dc\u05de\u05e8\u05d7\u05d1 \u05d4\u05de\u05d5\u05d2\u05df",
        "label": "Hostile aircraft (cat 6) \u2192 SHELTER",
    },
    {
        "cat": 7,
        "title": "\u05d7\u05d5\u05de\u05e8\u05d9\u05dd \u05de\u05e1\u05d5\u05db\u05e0\u05d9\u05dd",
        "desc": "\u05d4\u05d9\u05db\u05e0\u05e1\u05d5 \u05dc\u05de\u05e8\u05d7\u05d1 \u05d4\u05de\u05d5\u05d2\u05df",
        "label": "Hazardous materials (cat 7) \u2192 SHELTER",
    },
    {
        "cat": 13,
        "title": "\u05d7\u05d3\u05d9\u05e8\u05ea \u05de\u05d7\u05d1\u05dc\u05d9\u05dd",
        "desc": "\u05d4\u05d9\u05db\u05e0\u05e1\u05d5 \u05dc\u05de\u05e8\u05d7\u05d1 \u05de\u05d5\u05d2\u05df \u05d5\u05e0\u05e2\u05dc\u05d5 \u05d0\u05ea \u05d4\u05d3\u05dc\u05ea",
        "label": "Terrorist infiltration (cat 13) \u2192 SHELTER",
    },
    {
        "cat": 10,
        "title": "\u05d1\u05d3\u05e7\u05d5\u05ea \u05d4\u05e7\u05e8\u05d5\u05d1\u05d5\u05ea \u05e6\u05e4\u05d5\u05d9\u05d5\u05ea \u05dc\u05d4\u05ea\u05e7\u05d1\u05dc \u05d4\u05ea\u05e8\u05e2\u05d5\u05ea \u05d1\u05d0\u05d6\u05d5\u05e8\u05da",
        "desc": "\u05e2\u05dc \u05ea\u05d5\u05e9\u05d1\u05d9 \u05d4\u05d0\u05d6\u05d5\u05e8\u05d9\u05dd \u05d4\u05d1\u05d0\u05d9\u05dd \u05dc\u05e9\u05e4\u05e8 \u05d0\u05ea \u05d4\u05de\u05d9\u05e7\u05d5\u05dd \u05dc\u05de\u05d9\u05d2\u05d5\u05df \u05d4\u05de\u05d9\u05d8\u05d1\u05d9 \u05d1\u05e7\u05e8\u05d1\u05ea\u05da. \u05d1\u05de\u05e7\u05e8\u05d4 \u05e9\u05dc \u05e7\u05d1\u05dc\u05ea \u05d4\u05ea\u05e8\u05e2\u05d4, \u05d9\u05e9 \u05dc\u05d4\u05d9\u05db\u05e0\u05e1 \u05dc\u05de\u05e8\u05d7\u05d1 \u05d4\u05de\u05d5\u05d2\u05df \u05d5\u05dc\u05e9\u05d4\u05d5\u05ea \u05d1\u05d5 \u05e2\u05d3 \u05dc\u05d4\u05d5\u05d3\u05e2\u05d4 \u05d7\u05d3\u05e9\u05d4.",
        "label": "Early warning (cat 10) \u2192 WARNING",
    },
    {
        "cat": 10,
        "title": "\u05d4\u05d0\u05d9\u05e8\u05d5\u05e2 \u05d4\u05e1\u05ea\u05d9\u05d9\u05dd",
        "desc": "\u05d4\u05e9\u05d5\u05d4\u05d9\u05dd \u05d1\u05de\u05e8\u05d7\u05d1 \u05d4\u05de\u05d5\u05d2\u05df \u05d9\u05db\u05d5\u05dc\u05d9\u05dd \u05dc\u05e6\u05d0\u05ea. \u05d1\u05e2\u05ea \u05e7\u05d1\u05dc\u05ea \u05d4\u05e0\u05d7\u05d9\u05d4 \u05d0\u05d5 \u05d4\u05ea\u05e8\u05e2\u05d4, \u05d9\u05e9 \u05dc\u05e4\u05e2\u05d5\u05dc \u05d1\u05d4\u05ea\u05d0\u05dd \u05dc\u05d4\u05e0\u05d7\u05d9\u05d5\u05ea \u05e4\u05d9\u05e7\u05d5\u05d3 \u05d4\u05e2\u05d5\u05e8\u05e3.",
        "label": "Event ended (cat 10) \u2192 CLEAR",
    },
]


def load_areas():
    """Load area->cities mapping from areas.json."""
    try:
        with open(AREAS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("areas", {})
    except Exception as e:
        print(f"Warning: Could not load {AREAS_FILE}: {e}")
        return {}


def fetch_monitored_cities(ip):
    """Fetch the device's monitored cities via /api/cities."""
    url = f"http://{ip}/api/cities"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            cities = data.get("cities", [])
            test_mode = data.get("test_mode", False)
            return cities, test_mode
    except Exception as e:
        print(f"Warning: Could not fetch cities from device: {e}")
        return [], False


def send_alert(ip, payload):
    """Send an alert payload to the ESP32 test injection endpoint."""
    url = f"http://{ip}/test_inject"
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            result = resp.read().decode()
            print(f"  -> {resp.status}: {result}")
    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.fp else ""
        print(f"  -> HTTP {e.code}: {body}")
        if e.code == 403:
            print("     Device is not in Test Mode! Enable it via web UI -> Test -> Enter Test Mode")
    except Exception as e:
        print(f"  -> Error: {e}")
        print("     Check that the device is reachable and in Test Mode")


def pick_from_list(items, prompt, allow_back=True):
    """Display a numbered list and let user pick. Returns index or -1 for back."""
    page_size = 20
    page = 0
    total_pages = (len(items) + page_size - 1) // page_size

    while True:
        start = page * page_size
        end = min(start + page_size, len(items))
        print(f"\n{prompt} (page {page+1}/{total_pages}, showing {start+1}-{end} of {len(items)}):\n")
        for i in range(start, end):
            print(f"  {i+1}. {items[i]}")
        print()
        if total_pages > 1:
            print("  n = next page, p = prev page")
        if allow_back:
            print("  b = back")
        print()

        choice = input("Enter choice: ").strip().lower()
        if choice == "b" and allow_back:
            return -1
        if choice == "n" and page < total_pages - 1:
            page += 1
            continue
        if choice == "p" and page > 0:
            page -= 1
            continue
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(items):
                return idx
        except ValueError:
            pass
        print("Invalid choice, try again")


def build_custom_alert(areas_data, monitored_cities):
    """Interactive flow: pick area -> city -> alert type -> send."""
    area_names = sorted(areas_data.keys())
    if not area_names:
        print("No areas data loaded! Make sure areas.json exists.")
        return None

    # Step 1: Pick area
    idx = pick_from_list(area_names, "Choose an area")
    if idx < 0:
        return None
    area = area_names[idx]
    cities = areas_data[area]
    print(f"\n  Selected area: {area}")

    # Step 2: Pick city
    idx = pick_from_list(cities, f"Choose a city in {area}")
    if idx < 0:
        return None
    city = cities[idx]
    print(f"  Selected city: {city}")

    # Warn if city is not monitored
    if city not in monitored_cities:
        print(f"\n  \u26a0  WARNING: '{city}' is NOT in the device's monitored cities!")
        print(f"     Monitored: {monitored_cities}")
        print(f"     The device should correctly IGNORE this alert.")
        ans = input("     Send anyway? (y/n): ").strip().lower()
        if ans != "y":
            return None

    # Step 3: Pick alert type
    labels = [at["label"] for at in ALERT_TYPES]
    idx = pick_from_list(labels, "Choose alert type")
    if idx < 0:
        return None
    at = ALERT_TYPES[idx]
    print(f"  Selected: {at['label']}")

    # Build payload
    payload = {
        "id": str(int(time.time() * 10000000)),
        "cat": at["cat"],
        "title": at["title"],
        "data": [city],
        "desc": at["desc"],
    }
    return payload


def build_quick_payload(alert_type_idx, city):
    """Build a payload using the given ALERT_TYPES index and city."""
    at = ALERT_TYPES[alert_type_idx]
    return {
        "id": str(int(time.time() * 10000000)),
        "cat": at["cat"],
        "title": at["title"],
        "data": [city],
        "desc": at["desc"],
    }


def main():
    if len(sys.argv) < 2:
        print("Usage: python test_alerts.py <ESP32_IP>")
        print("Example: python test_alerts.py 192.168.1.100")
        sys.exit(1)

    ip = sys.argv[1]
    areas_data = load_areas()

    print(f"ESP32 Alert Test Injector")
    print(f"Target: {ip}")

    # Fetch monitored cities from device
    monitored_cities, test_mode = fetch_monitored_cities(ip)
    if monitored_cities:
        print(f"Monitored cities: {', '.join(monitored_cities)}")
    else:
        print("WARNING: Could not fetch monitored cities from device!")
    if not test_mode:
        print("\u26a0  Device is NOT in Test Mode! Enable it via web UI -> Test -> Enter Inject Mode")

    if areas_data:
        print(f"Loaded {len(areas_data)} areas from areas.json")
    print()

    # Default city for quick options = first monitored city
    default_city = monitored_cities[0] if monitored_cities else None

    while True:
        print("=" * 60)
        if default_city:
            print(f"Quick alerts target: {default_city}")
        print("Main menu:\n")
        print("  1. Custom alert (choose area \u2192 city \u2192 alert type)")
        if default_city:
            print(f"  2. Missiles \u2192 SHELTER          ({default_city})")
            print(f"  3. Earthquake \u2192 SHELTER         ({default_city})")
            print(f"  4. Hostile aircraft \u2192 SHELTER    ({default_city})")
            print(f"  5. Terrorist attack \u2192 SHELTER   ({default_city})")
            print(f"  6. Early warning \u2192 WARNING      ({default_city})")
            print(f"  7. Event ended \u2192 CLEAR          ({default_city})")
        else:
            print("  2-7. (unavailable \u2014 no monitored cities found)")
        print("  8. Clear alert (empty payload) \u2192 IDLE")
        print("  9. Full scenario: WARNING \u2192 SHELTER \u2192 CLEAR (auto)")
        print("\n  0. Exit\n")

        choice = input("Enter choice: ").strip()
        if choice == "0":
            print("Bye!")
            break

        payload = None

        if choice == "1":
            payload = build_custom_alert(areas_data, monitored_cities)
            if payload is None:
                continue

        elif choice in ("2", "3", "4", "5", "6", "7") and default_city:
            # Map choices to ALERT_TYPES indices:
            # 2=missiles(0), 3=earthquake(1), 4=aircraft(2),
            # 5=hazmat... wait, let me use correct mapping
            type_map = {"2": 0, "3": 1, "4": 2, "5": 4, "6": 5, "7": 6}
            payload = build_quick_payload(type_map[choice], default_city)

        elif choice == "8":
            payload = {}

        elif choice == "9" and default_city:
            # Full realistic scenario: WARNING -> wait -> SHELTER -> wait -> CLEAR
            print(f"\n  Running full scenario for {default_city}...")
            print(f"  Step 1/3: Early warning (WARNING)")
            send_alert(ip, build_quick_payload(5, default_city))
            print(f"  Waiting 8 seconds...")
            time.sleep(8)
            print(f"  Step 2/3: Missiles (SHELTER)")
            send_alert(ip, build_quick_payload(0, default_city))
            print(f"  Waiting 15 seconds...")
            time.sleep(15)
            print(f"  Step 3/3: Event ended (CLEAR)")
            send_alert(ip, build_quick_payload(6, default_city))
            print(f"  Scenario complete!\n")
            continue

        else:
            print("Invalid choice\n")
            continue

        # Display what we're sending
        if payload:
            print(f"\nSending alert:")
            print(f"  cat={payload.get('cat','?')}  title={payload.get('title','')}")
            print(f"  cities={payload.get('data',[])}")
            print(f"  desc={payload.get('desc','')}")
        else:
            print(f"\nSending: clear (empty payload)")

        send_alert(ip, payload)
        print()


if __name__ == "__main__":
    main()
