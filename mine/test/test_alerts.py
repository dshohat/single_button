"""
Test Alert Injector for ESP32 Emergency Alert System

Sends realistic Pikud HaOref-format JSON to the ESP32's /test_inject endpoint.
The device must be in Test Mode (enable via web UI -> Test page).

Usage:
    python test_alerts.py <ESP32_IP>
    python test_alerts.py 192.168.1.100
"""

import sys
import json
import os
import urllib.request

# Bypass proxy for local ESP32 connections
urllib.request.install_opener(
    urllib.request.build_opener(urllib.request.ProxyHandler({}))
)

# Load areas data from the project's areas.json
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AREAS_FILE = os.path.join(SCRIPT_DIR, "..", "arduino", "data", "areas.json")

ALERT_TYPES = [
    {"cat": 1,  "title": "\u05d9\u05e8\u05d9 \u05e8\u05e7\u05d8\u05d5\u05ea \u05d5\u05d8\u05d9\u05dc\u05d9\u05dd",
     "desc": "\u05d4\u05d9\u05db\u05e0\u05e1\u05d5 \u05dc\u05de\u05e8\u05d7\u05d1 \u05d4\u05de\u05d5\u05d2\u05df \u05d5\u05e9\u05d4\u05d5 \u05d1\u05d5 10 \u05d3\u05e7\u05d5\u05ea",
     "label": "Missiles (cat 1) - SIREN"},
    {"cat": 3,  "title": "\u05e8\u05e2\u05d9\u05d3\u05ea \u05d0\u05d3\u05de\u05d4",
     "desc": "\u05e6\u05d0\u05d5 \u05de\u05d4\u05de\u05d1\u05e0\u05d9\u05dd \u05dc\u05e9\u05d8\u05d7 \u05e4\u05ea\u05d5\u05d7",
     "label": "Earthquake (cat 3) - SIREN"},
    {"cat": 6,  "title": "\u05d7\u05d3\u05d9\u05e8\u05ea \u05db\u05dc\u05d9 \u05d8\u05d9\u05e1 \u05e2\u05d5\u05d9\u05d9\u05df",
     "desc": "\u05d4\u05d9\u05db\u05e0\u05e1\u05d5 \u05dc\u05de\u05e8\u05d7\u05d1 \u05d4\u05de\u05d5\u05d2\u05df",
     "label": "Hostile aircraft (cat 6) - SIREN"},
    {"cat": 7,  "title": "\u05d7\u05d5\u05de\u05e8\u05d9\u05dd \u05de\u05e1\u05d5\u05db\u05e0\u05d9\u05dd",
     "desc": "\u05d4\u05d9\u05db\u05e0\u05e1\u05d5 \u05dc\u05de\u05e8\u05d7\u05d1 \u05d4\u05de\u05d5\u05d2\u05df",
     "label": "Hazardous materials (cat 7) - SIREN"},
    {"cat": 13, "title": "\u05d7\u05d3\u05d9\u05e8\u05ea \u05de\u05d7\u05d1\u05dc\u05d9\u05dd",
     "desc": "\u05d4\u05d9\u05db\u05e0\u05e1\u05d5 \u05dc\u05de\u05e8\u05d7\u05d1 \u05de\u05d5\u05d2\u05df \u05d5\u05e0\u05e2\u05dc\u05d5 \u05d0\u05ea \u05d4\u05d3\u05dc\u05ea",
     "label": "Terrorist infiltration (cat 13) - SIREN"},
    {"cat": 10, "title": "\u05d4\u05ea\u05e8\u05d0\u05d4 \u05de\u05d5\u05e7\u05d3\u05de\u05ea",
     "desc": "\u05d4\u05d9\u05db\u05e0\u05e1\u05d5 \u05dc\u05de\u05e8\u05d7\u05d1 \u05d4\u05de\u05d5\u05d2\u05df",
     "label": "NewsFlash early warning (cat 10) - FLASH"},
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


def build_custom_alert(areas_data):
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

    # Step 3: Pick alert type
    labels = [at["label"] for at in ALERT_TYPES]
    idx = pick_from_list(labels, "Choose alert type")
    if idx < 0:
        return None
    at = ALERT_TYPES[idx]
    print(f"  Selected: {at['label']}")

    # Build payload
    payload = {
        "id": str(int(__import__("time").time() * 10000000)),
        "cat": at["cat"],
        "title": at["title"],
        "data": [city],
        "desc": at["desc"],
    }
    return payload


def main():
    if len(sys.argv) < 2:
        print("Usage: python test_alerts.py <ESP32_IP>")
        print("Example: python test_alerts.py 192.168.1.100")
        sys.exit(1)

    ip = sys.argv[1]
    areas_data = load_areas()

    print(f"ESP32 Alert Test Injector")
    print(f"Target: {ip}")
    if areas_data:
        print(f"Loaded {len(areas_data)} areas with cities")
    print(f"Make sure the device is in Test Mode first!\n")

    while True:
        print("=" * 55)
        print("Main menu:\n")
        print("  1. Custom alert (choose area -> city -> alert type)")
        print("  2. Quick: Missiles in Tel Aviv (cat 1)")
        print("  3. Quick: Earthquake in Jerusalem (cat 3)")
        print("  4. Quick: Hostile aircraft in Haifa (cat 6)")
        print("  5. Quick: Terrorist infiltration in Sderot (cat 13)")
        print("  6. NewsFlash early warning (cat 10)")
        print("  7. NewsFlash event ended (\u05d4\u05e1\u05ea\u05d9\u05d9\u05dd) - back to normal")
        print("  8. Clear alert (empty) - back to normal")
        print("\n  0. Exit\n")

        choice = input("Enter choice: ").strip()
        if choice == "0":
            print("Bye!")
            break

        payload = None

        if choice == "1":
            payload = build_custom_alert(areas_data)
            if payload is None:
                continue

        elif choice == "2":
            payload = {
                "id": str(int(__import__("time").time() * 10000000)),
                "cat": 1,
                "title": "\u05d9\u05e8\u05d9 \u05e8\u05e7\u05d8\u05d5\u05ea \u05d5\u05d8\u05d9\u05dc\u05d9\u05dd",
                "data": ["\u05ea\u05dc \u05d0\u05d1\u05d9\u05d1 - \u05de\u05e8\u05db\u05d6 \u05d4\u05e2\u05d9\u05e8"],
                "desc": "\u05d4\u05d9\u05db\u05e0\u05e1\u05d5 \u05dc\u05de\u05e8\u05d7\u05d1 \u05d4\u05de\u05d5\u05d2\u05df \u05d5\u05e9\u05d4\u05d5 \u05d1\u05d5 10 \u05d3\u05e7\u05d5\u05ea",
            }
        elif choice == "3":
            payload = {
                "id": str(int(__import__("time").time() * 10000000)),
                "cat": 3,
                "title": "\u05e8\u05e2\u05d9\u05d3\u05ea \u05d0\u05d3\u05de\u05d4",
                "data": ["\u05d9\u05e8\u05d5\u05e9\u05dc\u05d9\u05dd - \u05de\u05e8\u05db\u05d6"],
                "desc": "\u05e6\u05d0\u05d5 \u05de\u05d4\u05de\u05d1\u05e0\u05d9\u05dd \u05dc\u05e9\u05d8\u05d7 \u05e4\u05ea\u05d5\u05d7",
            }
        elif choice == "4":
            payload = {
                "id": str(int(__import__("time").time() * 10000000)),
                "cat": 6,
                "title": "\u05d7\u05d3\u05d9\u05e8\u05ea \u05db\u05dc\u05d9 \u05d8\u05d9\u05e1 \u05e2\u05d5\u05d9\u05d9\u05df",
                "data": ["\u05d7\u05d9\u05e4\u05d4 - \u05db\u05e8\u05de\u05dc \u05d5\u05e2\u05d9\u05e8 \u05ea\u05d7\u05ea\u05d9\u05ea"],
                "desc": "\u05d4\u05d9\u05db\u05e0\u05e1\u05d5 \u05dc\u05de\u05e8\u05d7\u05d1 \u05d4\u05de\u05d5\u05d2\u05df",
            }
        elif choice == "5":
            payload = {
                "id": str(int(__import__("time").time() * 10000000)),
                "cat": 13,
                "title": "\u05d7\u05d3\u05d9\u05e8\u05ea \u05de\u05d7\u05d1\u05dc\u05d9\u05dd",
                "data": ["\u05e9\u05d3\u05e8\u05d5\u05ea, \u05d0\u05d9\u05d1\u05d9\u05dd, \u05e0\u05d9\u05e8 \u05e2\u05dd"],
                "desc": "\u05d4\u05d9\u05db\u05e0\u05e1\u05d5 \u05dc\u05de\u05e8\u05d7\u05d1 \u05de\u05d5\u05d2\u05df \u05d5\u05e0\u05e2\u05dc\u05d5 \u05d0\u05ea \u05d4\u05d3\u05dc\u05ea",
            }
        elif choice == "6":
            payload = {
                "id": str(int(__import__("time").time() * 10000000)),
                "cat": 10,
                "title": "\u05d4\u05ea\u05e8\u05d0\u05d4 \u05de\u05d5\u05e7\u05d3\u05de\u05ea",
                "data": ["\u05d2\u05d5\u05e9 \u05d3\u05df"],
                "desc": "\u05d4\u05d9\u05db\u05e0\u05e1\u05d5 \u05dc\u05de\u05e8\u05d7\u05d1 \u05d4\u05de\u05d5\u05d2\u05df",
            }
        elif choice == "7":
            payload = {
                "id": str(int(__import__("time").time() * 10000000)),
                "cat": 10,
                "title": "\u05d4\u05d0\u05d9\u05e8\u05d5\u05e2 \u05d4\u05e1\u05ea\u05d9\u05d9\u05dd",
                "data": ["\u05d2\u05d5\u05e9 \u05d3\u05df"],
                "desc": "\u05e0\u05d9\u05ea\u05df \u05dc\u05e6\u05d0\u05ea \u05de\u05d4\u05de\u05e8\u05d7\u05d1 \u05d4\u05de\u05d5\u05d2\u05df",
            }
        elif choice == "8":
            payload = {}
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
